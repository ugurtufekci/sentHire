"""Stage 2 (compile) + Stages 3–6 (screening run) worker tasks.

Orchestration model (docs/02, docs/08 §2): DB is the state machine; Celery
messages are hints. Phase transitions are claimed with guarded UPDATEs so the
last-finishing worker (and only it) advances the run:

    queued → screening → selecting → deep_analysis → scoring → complete
                                   └────────────── (no deep needed) ─┘
"""

import uuid
from datetime import UTC, datetime

import anthropic
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from senthire import PIPELINE_VERSION
from senthire.compiler.compiler import CompilationFailed, compile_spec
from senthire.config import get_settings
from senthire.db.models import (
    Application,
    AuditLog,
    CandidateProfileRow,
    Evaluation,
    EvaluationSpecRow,
    Job,
    JobTemplate,
    RequirementResult,
    ScreeningRun,
)
from senthire.db.session import get_sessionmaker
from senthire.domain.anchors import discrimination_report
from senthire.domain.ranking import rank_key
from senthire.domain.scoring import score as run_scorer
from senthire.domain.spec import EvaluationSpec
from senthire.screening import batch
from senthire.screening.assemble import (
    build_result_document,
    judgments_to_verdicts,
    merge_verdicts,
    verdicts_from_result_document,
)
from senthire.screening.deterministic import run_deterministic_stage
from senthire.screening.evidence import verify_all
from senthire.screening.llm import ScreeningCallFailed, deep_analyze, light_screen
from senthire.screening.pricing import estimate_usd
from senthire.screening.schemas import DeepAnalysisOutput, LightScreenOutput
from senthire.screening.selection import Preliminary, select_for_deep
from senthire.workers.celery_app import celery_app

TRANSIENT = (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError)


def _session() -> Session:
    return get_sessionmaker()()


def _audit_llm(
    session: Session, org_id, run_id, stage: str, usage, transport: str = "interactive"
) -> None:
    session.add(
        AuditLog(
            org_id=org_id,
            actor=None,
            event="llm.call",
            entity={"type": "screening_run", "id": str(run_id)},
            detail={
                "stage": stage,
                "model": usage.model,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_read_tokens": usage.cache_read_tokens,
                "cache_write_tokens": usage.cache_write_tokens,
                # batch tokens are billed at 50% — recorded so cost rollups and
                # the UI can show what economy mode actually saved
                "transport": transport,
            },
        )
    )


# --------------------------------------------------------------------------- #
# Stage 2 — requirement compilation
# --------------------------------------------------------------------------- #


@celery_app.task(
    name="senthire.screen.compile_spec",
    autoretry_for=TRANSIENT,
    retry_backoff=5,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=5,
)
def compile_spec_task(spec_row_id: str) -> dict:
    settings = get_settings()
    session = _session()
    try:
        row = session.get(EvaluationSpecRow, uuid.UUID(spec_row_id))
        if row is None or row.status != "compiling":
            return {"status": "skipped"}

        template_spec = None
        job = session.get(Job, row.job_id)
        if job and job.template_id:
            template = session.get(JobTemplate, job.template_id)
            if template:
                template_spec = EvaluationSpec.model_validate(template.spec_seed)

        try:
            result = compile_spec(
                template_spec, row.source_nl_text or "", version=row.version, locale="tr"
            )
        except CompilationFailed as exc:
            row.status = "failed"
            row.spec = {"error": str(exc)}
            session.commit()
            return {"status": "failed", "error": str(exc)}

        spec_doc = result.spec.model_dump()
        spec_doc["compiler"] = {
            "model": settings.compiler_model,
            "prompt_version": settings.prompt_versions["compile"],
            "back_translation": result.back_translation,
            "clarifications": result.clarifications,
            "compliance_flags": result.compliance_flags,
            "warnings": result.warnings,
            "usage": result.usage,
        }
        row.spec = spec_doc
        row.status = "draft"
        session.add(
            AuditLog(
                org_id=row.org_id,
                actor=None,
                event="spec.compiled",
                entity={"type": "evaluation_spec", "id": str(row.id)},
                detail={"version": row.version, "warnings": result.warnings,
                        "usage": result.usage},
            )
        )
        session.commit()
        return {"status": "draft", "spec_id": str(row.id)}
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Run orchestration
# --------------------------------------------------------------------------- #


def _load_run_context(session: Session, run_id: uuid.UUID):
    run = session.get(ScreeningRun, run_id)
    if run is None:
        return None, None
    spec_row = session.get(EvaluationSpecRow, run.spec_id)
    spec = EvaluationSpec.model_validate({k: v for k, v in spec_row.spec.items() if k != "compiler"})
    return run, spec


def _profile_for_application(session: Session, app: Application) -> CandidateProfileRow | None:
    return session.scalar(
        select(CandidateProfileRow)
        .where(CandidateProfileRow.document_id == app.document_id)
        .order_by(CandidateProfileRow.version.desc())
        .limit(1)
    )


@celery_app.task(name="senthire.screen.run_start")
def run_start(run_id: str) -> dict:
    session = _session()
    try:
        run, spec = _load_run_context(session, uuid.UUID(run_id))
        if run is None or run.status != "queued":
            return {"status": "skipped"}

        apps = session.scalars(
            select(Application).where(
                Application.job_id == run.job_id,
                Application.status.in_(["profiled", "screened", "shortlisted", "rejected"]),
            )
        ).all()

        pending: list[Application] = []
        memoized = 0
        for app in apps:
            profile_row = _profile_for_application(session, app)
            if profile_row is None:
                continue
            # Cross-run memoization: same profile+spec+pipeline → copy forward.
            prior = session.scalar(
                select(Evaluation)
                .where(
                    Evaluation.application_id == app.id,
                    Evaluation.profile_version == profile_row.version,
                    Evaluation.spec_version == _spec_version(session, run.spec_id),
                    Evaluation.pipeline_version == PIPELINE_VERSION,
                    Evaluation.run_id != run.id,
                )
                .order_by(Evaluation.created_at.desc())
                .limit(1)
            )
            if prior is not None:
                session.add(
                    Evaluation(
                        org_id=run.org_id,
                        run_id=run.id,
                        application_id=app.id,
                        profile_version=prior.profile_version,
                        spec_version=prior.spec_version,
                        pipeline_version=prior.pipeline_version,
                        stage_reached=prior.stage_reached,
                        hard_result=prior.hard_result,
                        overall_score=prior.overall_score,
                        confidence=prior.confidence,
                        result=prior.result,
                        models_used=prior.models_used,
                    )
                )
                memoized += 1
            else:
                pending.append(app)

        # Merge, never replace: the run may already carry marks set when it was
        # created (the offline-demo stamp, for one), and losing them would let a
        # demo result be read as a real screening.
        run.funnel = {
            **(run.funnel or {}),
            "total": len(pending) + memoized,
            "memoized": memoized,
            "deep_pending": [],
        }
        run.status = "screening"
        run.started_at = datetime.now(UTC)
        session.commit()

        if run.mode == "batch":
            return _start_light_batch(session, run, spec, pending, memoized)

        for app in pending:
            screen_application.delay(run_id, str(app.id))
        if not pending:
            _try_advance(session, run.id, "screening", "selecting", finalize_run, run_id)
        return {"status": "started", "pending": len(pending), "memoized": memoized}
    finally:
        session.close()


def _start_light_batch(
    session: Session,
    run: ScreeningRun,
    spec: EvaluationSpec,
    pending: list[Application],
    memoized: int,
) -> dict:
    """Economy mode: run Stage 3 here, then submit every Stage 4 call as one batch.

    Clean deterministic knockouts are persisted immediately — they cost nothing
    and must not occupy a batch slot.
    """
    requests = []
    knocked_out = 0
    for app in pending:
        profile_row = _profile_for_application(session, app)
        if profile_row is None:
            continue
        det = run_deterministic_stage(spec, profile_row.profile)
        if det.knocked_out and not det.borderline:
            _persist_light_evaluation(session, run, spec, app, profile_row, det, None, None, None)
            knocked_out += 1
            continue
        requests.append(batch.light_request(str(app.id), spec, profile_row.profile))
    session.commit()

    if not requests:
        _try_advance(session, run.id, "screening", "selecting", finalize_run, str(run.id))
        return {"status": "started", "mode": "batch", "submitted": 0, "knocked_out": knocked_out}

    batch_id = batch.submit(requests)
    funnel = dict(run.funnel or {})
    funnel["batch"] = {"light": {"id": batch_id, "submitted": len(requests), "polls": 0}}
    run.funnel = funnel
    session.commit()

    poll_batch.apply_async(
        args=[str(run.id), "light", batch_id],
        countdown=get_settings().batch_poll_initial_seconds,
    )
    return {
        "status": "started",
        "mode": "batch",
        "submitted": len(requests),
        "knocked_out": knocked_out,
        "memoized": memoized,
    }


def _spec_version(session: Session, spec_id: uuid.UUID) -> int:
    row = session.get(EvaluationSpecRow, spec_id)
    return row.version if row else -1


def _try_advance(session: Session, run_id, from_status: str, to_status: str, task, *args) -> bool:
    """Guarded phase transition: exactly one worker wins and enqueues the next task."""
    claimed = session.execute(
        update(ScreeningRun)
        .where(ScreeningRun.id == run_id, ScreeningRun.status == from_status)
        .values(status=to_status)
    )
    session.commit()
    if claimed.rowcount == 1:
        task.delay(*args)
        return True
    return False


def _maybe_finish_light_phase(session: Session, run: ScreeningRun) -> None:
    done = session.scalar(
        select(func.count()).select_from(Evaluation).where(Evaluation.run_id == run.id)
    )
    if done >= (run.funnel or {}).get("total", 0):
        _try_advance(session, run.id, "screening", "selecting", finalize_run, str(run.id))


def _persist_light_evaluation(
    session: Session,
    run: ScreeningRun,
    spec: EvaluationSpec,
    app: Application,
    profile_row: CandidateProfileRow,
    det,
    output,
    usage,
    light_failed: str | None,
) -> str:
    """Merge + score + persist one candidate's preliminary evaluation.

    Shared by the interactive and batch transports so a candidate's stored
    evaluation is identical either way — only the delivery of `output` differs.
    Returns the stage reached.
    """
    settings = get_settings()
    models_used: dict = {}
    narrative: dict = {}
    evidence_stats: dict = {}
    light_verdicts = None

    if det.knocked_out and not det.borderline:
        stage_reached = "hard_filter"
        hard_result = "fail"
    else:
        if output is not None:
            if usage is not None:
                _audit_llm(session, run.org_id, run.id, "light", usage, run.mode)
            source_text = profile_row.raw_text + "\n" + str(profile_row.profile)
            judgments, evidence_stats = verify_all(output.judgments, source_text)
            light_verdicts = judgments_to_verdicts(judgments, "light")
            narrative = {
                "strengths": output.strengths,
                "weaknesses": output.weaknesses,
                "red_flags": output.red_flags,
            }
            models_used["light"] = settings.light_screen_model
        stage_reached = "light"
        hard_result = "borderline" if (det.borderline or det.knocked_out) else "pass"

    verdicts = merge_verdicts(spec, det.verdicts, light_verdicts)
    score_result = run_scorer(spec, verdicts)
    if score_result.gate.status == "fail":
        hard_result = "fail" if not det.borderline else "borderline"

    result_doc = build_result_document(
        spec,
        verdicts,
        score_result,
        stage_reached=stage_reached,
        narrative=narrative,
        evidence_stats=evidence_stats,
        models_used=models_used,
    )
    if light_failed is not None:
        result_doc["needs_review"] = True
        result_doc["review_reasons"] = sorted(
            set(result_doc.get("review_reasons", [])) | {"light_screen_failed"}
        )
        result_doc["light_error"] = light_failed

    _carry_integrity(result_doc, profile_row)
    session.add(
        Evaluation(
            org_id=run.org_id,
            run_id=run.id,
            application_id=app.id,
            profile_version=profile_row.version,
            spec_version=spec.version,
            pipeline_version=PIPELINE_VERSION,
            stage_reached=stage_reached,
            hard_result=hard_result,
            overall_score=score_result.final_score,
            confidence=score_result.confidence,
            result=result_doc,
            models_used=models_used,
        )
    )
    return stage_reached


@celery_app.task(
    name="senthire.screen.application",
    autoretry_for=TRANSIENT,
    retry_backoff=5,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=5,
)
def screen_application(run_id: str, application_id: str) -> dict:
    """Stages 3+4 for one candidate; writes the preliminary evaluation."""
    session = _session()
    try:
        run, spec = _load_run_context(session, uuid.UUID(run_id))
        if run is None or run.status not in {"screening", "selecting"}:
            return {"status": "skipped"}
        app = session.get(Application, uuid.UUID(application_id))
        existing = session.scalar(
            select(Evaluation).where(
                Evaluation.run_id == run.id, Evaluation.application_id == app.id
            )
        )
        if existing is not None:  # redelivery — already done
            return {"status": "already_evaluated"}

        profile_row = _profile_for_application(session, app)
        det = run_deterministic_stage(spec, profile_row.profile)

        output = usage = None
        light_failed: str | None = None
        if not (det.knocked_out and not det.borderline):
            try:
                output, usage = light_screen(spec, profile_row.profile)
            except ScreeningCallFailed as exc:
                # One candidate's permanent model failure must never stall the run
                # (docs/08 §6): keep the deterministic verdicts, flag for review.
                light_failed = str(exc)

        stage_reached = _persist_light_evaluation(
            session, run, spec, app, profile_row, det, output, usage, light_failed
        )
        session.commit()

        _maybe_finish_light_phase(session, run)
        return {"status": "evaluated", "stage": stage_reached}
    finally:
        session.close()


@celery_app.task(name="senthire.screen.finalize")
def finalize_run(run_id: str) -> dict:
    """Stage 5 selection: decide who gets deep analysis (docs/02 Stage 5)."""
    settings = get_settings()
    session = _session()
    try:
        run, spec = _load_run_context(session, uuid.UUID(run_id))
        if run is None or run.status != "selecting":
            return {"status": "skipped"}

        evaluations = session.scalars(
            select(Evaluation).where(Evaluation.run_id == run.id)
        ).all()
        prelims = []
        for ev in evaluations:
            if ev.stage_reached == "hard_filter":
                continue  # clean knockouts never get deep spend
            verdicts = verdicts_from_result_document(ev.result)
            prelims.append(
                Preliminary(
                    application_id=str(ev.application_id),
                    score_result=run_scorer(spec, verdicts),
                    verdicts=verdicts,
                    borderline=ev.hard_result == "borderline",
                )
            )

        selected = select_for_deep(
            spec,
            prelims,
            top_k=settings.shortlist_top_k,
            band_extra=settings.deep_band_extra,
            confidence_threshold=settings.deep_confidence_threshold,
            weight_threshold=settings.deep_weight_threshold,
        )
        funnel = dict(run.funnel or {})
        funnel["deep_pending"] = [p.application_id for p in selected]
        funnel["deep_reasons"] = {p.application_id: p.reasons for p in selected}
        run.funnel = funnel
        session.commit()

        if not selected:
            _try_advance(session, run.id, "selecting", "scoring", score_run, run_id)
            return {"status": "no_deep_needed"}

        _try_advance(session, run.id, "selecting", "deep_analysis", _noop_task, "noop")
        if run.mode == "batch":
            return _start_deep_batch(session, run, spec, [p.application_id for p in selected])
        for p in selected:
            deep_application.delay(run_id, p.application_id)
        return {"status": "deep_enqueued", "count": len(selected)}
    finally:
        session.close()


def _start_deep_batch(
    session: Session, run: ScreeningRun, spec: EvaluationSpec, application_ids: list[str]
) -> dict:
    """Economy mode for Stage 5: the shortlist goes out as a single batch."""
    requests = []
    for application_id in application_ids:
        ev = session.scalar(
            select(Evaluation).where(
                Evaluation.run_id == run.id,
                Evaluation.application_id == uuid.UUID(application_id),
            )
        )
        if ev is None or ev.stage_reached == "deep":
            continue
        profile_row = _profile_for_application(session, session.get(Application, ev.application_id))
        requests.append(
            batch.deep_request(
                application_id,
                spec,
                profile_row.profile,
                profile_row.raw_text,
                _light_judgments(ev),
            )
        )

    if not requests:
        _try_advance(session, run.id, "deep_analysis", "scoring", score_run, str(run.id))
        return {"status": "deep_batch_empty"}

    batch_id = batch.submit(requests)
    funnel = dict(run.funnel or {})
    batches = dict(funnel.get("batch") or {})
    batches["deep"] = {"id": batch_id, "submitted": len(requests), "polls": 0}
    funnel["batch"] = batches
    run.funnel = funnel
    session.commit()

    poll_batch.apply_async(
        args=[str(run.id), "deep", batch_id],
        countdown=get_settings().batch_poll_initial_seconds,
    )
    return {"status": "deep_batch_submitted", "count": len(requests)}


@celery_app.task(name="senthire.screen.noop")
def _noop_task(_: str) -> None:  # transition marker only
    return None


def _maybe_finish_deep_phase(session: Session, run: ScreeningRun) -> None:
    pending = set((run.funnel or {}).get("deep_pending", []))
    if not pending:
        _try_advance(session, run.id, "deep_analysis", "scoring", score_run, str(run.id))
        return
    deep_done = session.scalars(
        select(Evaluation.application_id).where(
            Evaluation.run_id == run.id, Evaluation.stage_reached == "deep"
        )
    ).all()
    if pending.issubset({str(a) for a in deep_done}):
        _try_advance(session, run.id, "deep_analysis", "scoring", score_run, str(run.id))


def _persist_deep_failure(ev: Evaluation, error: str) -> None:
    """Deep is an enhancement: a permanent failure keeps the light result,
    flagged for review rather than blocking the run (docs/08 §6)."""
    result = dict(ev.result)
    result["review_reasons"] = sorted(
        set(result.get("review_reasons", [])) | {"deep_analysis_failed"}
    )
    result["needs_review"] = True
    result["deep_error"] = error
    ev.result = result
    ev.stage_reached = "deep"


def _persist_deep_evaluation(
    session: Session,
    run: ScreeningRun,
    spec: EvaluationSpec,
    ev: Evaluation,
    profile_row: CandidateProfileRow,
    output,
    usage,
) -> int:
    """Verify + re-merge + re-score one deep result. Shared by both transports."""
    settings = get_settings()
    if usage is not None:
        _audit_llm(session, run.org_id, run.id, "deep", usage, run.mode)
    judgments, evidence_stats = verify_all(output.judgments, profile_row.raw_text)
    deep_verdicts = judgments_to_verdicts(judgments, "deep")

    det = run_deterministic_stage(spec, profile_row.profile)
    light_verdicts = {
        rid: v
        for rid, v in verdicts_from_result_document(ev.result).items()
        if v.source_stage == "light"
    }
    verdicts = merge_verdicts(spec, det.verdicts, light_verdicts, deep_verdicts)
    score_result = run_scorer(spec, verdicts)

    narrative = dict(ev.result.get("narrative") or {})
    narrative.update(
        {
            "strengths": output.strengths or narrative.get("strengths", []),
            "weaknesses": output.weaknesses or narrative.get("weaknesses", []),
            "missing_information": output.missing_information,
            "summary": output.summary,
        }
    )
    models_used = dict(ev.models_used or {})
    models_used["deep"] = settings.deep_analysis_model

    deep_reasons = (run.funnel or {}).get("deep_reasons", {}).get(str(ev.application_id), [])
    ev.result = build_result_document(
        spec,
        verdicts,
        score_result,
        stage_reached="deep",
        narrative=narrative,
        corrections=[c.model_dump() for c in output.corrections],
        deep_reasons=deep_reasons,
        evidence_stats=evidence_stats,
        models_used=models_used,
    )
    _carry_integrity(ev.result, profile_row)
    ev.stage_reached = "deep"
    ev.hard_result = "fail" if score_result.gate.status == "fail" else "pass"
    ev.overall_score = score_result.final_score
    ev.confidence = score_result.confidence
    ev.models_used = models_used
    return len(output.corrections)


def _carry_integrity(result_doc: dict, profile_row: CandidateProfileRow) -> None:
    """Attach the document's manipulation findings to a freshly built result.

    Called from every stage that builds a result document. Stage 5 rebuilds the
    document from scratch, so without this a candidate flagged at Stage 4 comes
    out of deep analysis clean — the flag silently deleted by the stage that was
    supposed to look harder.

    The score is never touched: the candidate is not penalized, the recruiter is
    informed (docs/09 §5).
    """
    integrity = (profile_row.profile or {}).get("integrity") or []
    if not integrity:
        return
    result_doc["integrity"] = integrity
    result_doc["needs_review"] = True
    result_doc["review_reasons"] = sorted(
        set(result_doc.get("review_reasons", [])) | {"prompt_injection_detected"}
    )


def _light_judgments(ev: Evaluation) -> list[dict]:
    return [r for r in ev.result.get("requirements", []) if r.get("source_stage") == "light"]


@celery_app.task(
    name="senthire.screen.deep",
    autoretry_for=TRANSIENT,
    retry_backoff=5,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=5,
)
def deep_application(run_id: str, application_id: str) -> dict:
    """Stage 5 for one selected candidate: verify + correct, then re-merge."""
    session = _session()
    try:
        run, spec = _load_run_context(session, uuid.UUID(run_id))
        if run is None or run.status != "deep_analysis":
            return {"status": "skipped"}
        ev = session.scalar(
            select(Evaluation).where(
                Evaluation.run_id == run.id,
                Evaluation.application_id == uuid.UUID(application_id),
            )
        )
        if ev is None or ev.stage_reached == "deep":
            _maybe_finish_deep_phase(session, run)
            return {"status": "skipped_or_done"}

        app = session.get(Application, ev.application_id)
        profile_row = _profile_for_application(session, app)

        try:
            output, usage = deep_analyze(
                spec, profile_row.profile, profile_row.raw_text, _light_judgments(ev)
            )
        except ScreeningCallFailed as exc:
            _persist_deep_failure(ev, str(exc))
            session.commit()
            run = session.get(ScreeningRun, run.id)
            _maybe_finish_deep_phase(session, run)
            return {"status": "deep_failed_kept_light"}

        corrections = _persist_deep_evaluation(
            session, run, spec, ev, profile_row, output, usage
        )
        session.commit()

        run = session.get(ScreeningRun, run.id)
        _maybe_finish_deep_phase(session, run)
        return {"status": "deep_done", "corrections": corrections}
    finally:
        session.close()


@celery_app.task(
    name="senthire.poll.batch",
    autoretry_for=TRANSIENT,
    retry_backoff=10,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=8,
)
def poll_batch(run_id: str, stage: str, batch_id: str) -> dict:
    """Wait for a submitted batch, then drain it into evaluations.

    Re-enqueues itself with a growing countdown while the batch is in flight.
    The re-enqueue (not a sleep) is what keeps a 24-hour-capable wait from
    holding a worker slot. Bounded by `batch_max_wait_seconds` so a stuck batch
    surfaces as a failed run instead of polling forever.
    """
    settings = get_settings()
    session = _session()
    try:
        run, spec = _load_run_context(session, uuid.UUID(run_id))
        expected_status = "screening" if stage == "light" else "deep_analysis"
        if run is None or run.status != expected_status:
            return {"status": "skipped"}

        status = batch.processing_status(batch_id)
        if status != "ended":
            funnel = dict(run.funnel or {})
            batches = dict(funnel.get("batch") or {})
            entry = dict(batches.get(stage) or {})
            polls = int(entry.get("polls", 0)) + 1
            entry.update({"polls": polls, "status": status})
            batches[stage] = entry
            funnel["batch"] = batches
            run.funnel = funnel
            session.commit()

            waited = polls * settings.batch_poll_interval_seconds
            if waited >= settings.batch_max_wait_seconds:
                _fail_run(session, run, f"{stage} batch did not finish within the wait budget")
                return {"status": "timed_out", "stage": stage}
            poll_batch.apply_async(
                args=[run_id, stage, batch_id],
                countdown=settings.batch_poll_interval_seconds,
            )
            return {"status": status, "stage": stage, "polls": polls}

        drained = (
            _drain_light_batch(session, run, spec, batch_id)
            if stage == "light"
            else _drain_deep_batch(session, run, spec, batch_id)
        )
        session.commit()

        run = session.get(ScreeningRun, run.id)
        if stage == "light":
            _try_advance(session, run.id, "screening", "selecting", finalize_run, run_id)
        else:
            _try_advance(session, run.id, "deep_analysis", "scoring", score_run, run_id)
        return {"status": "drained", "stage": stage, **drained}
    finally:
        session.close()


def _fail_run(session: Session, run: ScreeningRun, reason: str) -> None:
    run.status = "failed"
    run.finished_at = datetime.now(UTC)
    funnel = dict(run.funnel or {})
    funnel["error"] = reason
    run.funnel = funnel
    session.add(
        AuditLog(
            org_id=run.org_id,
            actor=None,
            event="run.failed",
            entity={"type": "screening_run", "id": str(run.id)},
            detail={"reason": reason},
        )
    )
    session.commit()


def _drain_light_batch(
    session: Session, run: ScreeningRun, spec: EvaluationSpec, batch_id: str
) -> dict:
    """Persist every Stage 4 batch result. Results arrive unordered — key by custom_id."""
    persisted = failed = 0
    for outcome in batch.iter_results(batch_id, LightScreenOutput):
        app = session.get(Application, uuid.UUID(outcome.custom_id))
        if app is None:
            continue
        existing = session.scalar(
            select(Evaluation).where(
                Evaluation.run_id == run.id, Evaluation.application_id == app.id
            )
        )
        if existing is not None:  # redelivered poll — already drained
            continue
        profile_row = _profile_for_application(session, app)
        det = run_deterministic_stage(spec, profile_row.profile)
        _persist_light_evaluation(
            session, run, spec, app, profile_row, det,
            outcome.output, outcome.usage, outcome.error,
        )
        if outcome.error:
            failed += 1
        else:
            persisted += 1
    return {"persisted": persisted, "failed": failed}


def _drain_deep_batch(
    session: Session, run: ScreeningRun, spec: EvaluationSpec, batch_id: str
) -> dict:
    persisted = failed = 0
    for outcome in batch.iter_results(batch_id, DeepAnalysisOutput):
        ev = session.scalar(
            select(Evaluation).where(
                Evaluation.run_id == run.id,
                Evaluation.application_id == uuid.UUID(outcome.custom_id),
            )
        )
        if ev is None or ev.stage_reached == "deep":
            continue
        if outcome.error or outcome.output is None:
            _persist_deep_failure(ev, outcome.error or "no output")
            failed += 1
            continue
        profile_row = _profile_for_application(session, session.get(Application, ev.application_id))
        _persist_deep_evaluation(
            session, run, spec, ev, profile_row, outcome.output, outcome.usage
        )
        persisted += 1
    return {"persisted": persisted, "failed": failed}


@celery_app.task(name="senthire.screen.score")
def score_run(run_id: str) -> dict:
    """Stage 6 — final deterministic scoring, ranking, persistence (docs/06)."""
    session = _session()
    try:
        run, spec = _load_run_context(session, uuid.UUID(run_id))
        if run is None or run.status != "scoring":
            return {"status": "skipped"}

        evaluations = session.scalars(
            select(Evaluation).where(Evaluation.run_id == run.id)
        ).all()

        rescored = []
        for ev in evaluations:
            verdicts = verdicts_from_result_document(ev.result)
            score_result = run_scorer(spec, verdicts)
            ev.overall_score = score_result.final_score
            ev.confidence = score_result.confidence
            ev.band = score_result.band
            ev.hard_result = "fail" if score_result.gate.status == "fail" else ev.hard_result
            result = dict(ev.result)
            result.update(
                {
                    "final_score": score_result.final_score,
                    "band": score_result.band,
                    "gate": score_result.gate.model_dump(),
                    "categories": {c: cs.model_dump() for c, cs in score_result.categories.items()},
                    "adjustments": [a.model_dump() for a in score_result.adjustments],
                    "needs_review": score_result.needs_review or result.get("needs_review", False),
                    "review_reasons": sorted(
                        set(score_result.review_reasons) | set(result.get("review_reasons", []))
                    ),
                }
            )
            ev.result = result
            rescored.append((ev, score_result))

        # rank gate-passed candidates; deterministic tie-break (docs/06)
        passed = [(ev, sr) for ev, sr in rescored if sr.gate.status == "pass"]
        passed.sort(
            key=lambda pair: rank_key(
                pair[1].final_score, pair[1].confidence, pair[0].application_id
            )
        )
        for rank, (ev, _) in enumerate(passed, start=1):
            ev.rank = rank
        for ev, sr in rescored:
            if sr.gate.status != "pass":
                ev.rank = None
                ev.band = "rejected"

        # normalized requirement_results (idempotent re-write)
        session.query(RequirementResult).filter(
            RequirementResult.evaluation_id.in_([ev.id for ev, _ in rescored])
        ).delete(synchronize_session=False)
        for ev, _ in rescored:
            for row in ev.result.get("requirements", []):
                session.add(
                    RequirementResult(
                        evaluation_id=ev.id,
                        req_id=row["req_id"],
                        verdict=row["verdict"],
                        score=row.get("score"),
                        confidence=row.get("confidence"),
                        info_status=row.get("info_status"),
                        evidence=row.get("evidence"),
                        source_stage=row.get("source_stage"),
                    )
                )

        session.execute(
            update(Application)
            .where(Application.id.in_([ev.application_id for ev, _ in rescored]))
            .values(status="screened")
        )

        # aggregate cost from the llm.call audit trail (docs/01 §6)
        cost: dict[str, dict] = {}
        for log in session.scalars(
            select(AuditLog).where(
                AuditLog.event == "llm.call",
                AuditLog.entity["id"].astext == str(run.id),
            )
        ).all():
            stage = log.detail.get("stage", "unknown")
            bucket = cost.setdefault(
                stage,
                {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "usd": 0.0,
                    "usd_saved": 0.0,
                },
            )
            bucket["calls"] += 1
            for key in ("input_tokens", "output_tokens", "cache_read_tokens"):
                bucket[key] += int(log.detail.get(key) or 0)
            full_price = estimate_usd(log.detail)
            batched = log.detail.get("transport") == "batch"
            discount = get_settings().batch_discount if batched else 0.0
            bucket["usd"] = round(bucket["usd"] + full_price * (1 - discount), 6)
            bucket["usd_saved"] = round(bucket["usd_saved"] + full_price * discount, 6)

        funnel = dict(run.funnel or {})
        funnel.update(
            {
                "evaluated": len(rescored),
                "hard_failed": sum(1 for ev, sr in rescored if sr.gate.status == "fail"),
                "deep_analyzed": sum(1 for ev, _ in rescored if ev.stage_reached == "deep"),
                "ranked": len(passed),
                # Which criteria actually separated people. Cheap to compute
                # here (the verdicts are in hand) and the only place that sees
                # the whole cohort at once.
                "consistency": discrimination_report(
                    spec, [(ev.result or {}).get("verdicts", {}) for ev, _ in rescored]
                ),
            }
        )
        run.funnel = funnel
        run.cost = cost
        run.status = "complete"
        run.finished_at = datetime.now(UTC)
        session.add(
            AuditLog(
                org_id=run.org_id,
                actor=None,
                event="run.completed",
                entity={"type": "screening_run", "id": str(run.id)},
                detail={"funnel": funnel, "cost": cost},
            )
        )
        session.commit()
        return {"status": "complete", "ranked": len(passed)}
    finally:
        session.close()
