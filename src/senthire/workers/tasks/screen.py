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
from senthire.domain.scoring import score as run_scorer
from senthire.domain.spec import EvaluationSpec
from senthire.screening.assemble import (
    build_result_document,
    judgments_to_verdicts,
    merge_verdicts,
    verdicts_from_result_document,
)
from senthire.screening.deterministic import run_deterministic_stage
from senthire.screening.evidence import verify_all
from senthire.screening.llm import ScreeningCallFailed, deep_analyze, light_screen
from senthire.screening.selection import Preliminary, select_for_deep
from senthire.workers.celery_app import celery_app

TRANSIENT = (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError)


def _session() -> Session:
    return get_sessionmaker()()


def _audit_llm(session: Session, org_id, run_id, stage: str, usage) -> None:
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
        run, _ = _load_run_context(session, uuid.UUID(run_id))
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

        run.funnel = {
            "total": len(pending) + memoized,
            "memoized": memoized,
            "deep_pending": [],
        }
        run.status = "screening"
        run.started_at = datetime.now(UTC)
        session.commit()

        for app in pending:
            screen_application.delay(run_id, str(app.id))
        if not pending:
            _try_advance(session, run.id, "screening", "selecting", finalize_run, run_id)
        return {"status": "started", "pending": len(pending), "memoized": memoized}
    finally:
        session.close()


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
    settings = get_settings()
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
        profile = profile_row.profile

        det = run_deterministic_stage(spec, profile)
        models_used: dict = {}
        narrative: dict = {}
        evidence_stats: dict = {}
        light_verdicts = None

        light_failed: str | None = None
        if det.knocked_out and not det.borderline:
            stage_reached = "hard_filter"
            hard_result = "fail"
        else:
            try:
                output, usage = light_screen(spec, profile)
            except ScreeningCallFailed as exc:
                # One candidate's permanent model failure must never stall the run
                # (docs/08 §6): keep the deterministic verdicts, flag for review.
                light_failed = str(exc)
                output, usage = None, None
            if output is not None:
                _audit_llm(session, run.org_id, run.id, "light", usage)
                source_text = profile_row.raw_text + "\n" + str(profile)
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
        for p in selected:
            deep_application.delay(run_id, p.application_id)
        return {"status": "deep_enqueued", "count": len(selected)}
    finally:
        session.close()


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
    settings = get_settings()
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
        profile = profile_row.profile

        light_judgments = [
            r for r in ev.result.get("requirements", []) if r.get("source_stage") == "light"
        ]
        try:
            output, usage = deep_analyze(spec, profile, profile_row.raw_text, light_judgments)
        except ScreeningCallFailed as exc:
            # deep is an enhancement: a permanent failure keeps the light result,
            # flagged for review rather than blocking the run (docs/08 §6)
            result = dict(ev.result)
            result.setdefault("review_reasons", []).append("deep_analysis_failed")
            result["needs_review"] = True
            result["deep_error"] = str(exc)
            ev.result = result
            ev.stage_reached = "deep"
            session.commit()
            run = session.get(ScreeningRun, run.id)
            _maybe_finish_deep_phase(session, run)
            return {"status": "deep_failed_kept_light"}

        _audit_llm(session, run.org_id, run.id, "deep", usage)
        judgments, evidence_stats = verify_all(output.judgments, profile_row.raw_text)
        deep_verdicts = judgments_to_verdicts(judgments, "deep")

        det = run_deterministic_stage(spec, profile)
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
        ev.stage_reached = "deep"
        ev.hard_result = "fail" if score_result.gate.status == "fail" else "pass"
        ev.overall_score = score_result.final_score
        ev.confidence = score_result.confidence
        ev.models_used = models_used
        session.commit()

        run = session.get(ScreeningRun, run.id)
        _maybe_finish_deep_phase(session, run)
        return {"status": "deep_done", "corrections": len(output.corrections)}
    finally:
        session.close()


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
            key=lambda pair: (
                -pair[1].final_score,
                -(pair[1].confidence or 0),
                str(pair[0].application_id),
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
        cost: dict[str, dict[str, int]] = {}
        for log in session.scalars(
            select(AuditLog).where(
                AuditLog.event == "llm.call",
                AuditLog.entity["id"].astext == str(run.id),
            )
        ).all():
            stage = log.detail.get("stage", "unknown")
            bucket = cost.setdefault(
                stage, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0}
            )
            bucket["calls"] += 1
            for key in ("input_tokens", "output_tokens", "cache_read_tokens"):
                bucket[key] += int(log.detail.get(key) or 0)

        funnel = dict(run.funnel or {})
        funnel.update(
            {
                "evaluated": len(rescored),
                "hard_failed": sum(1 for ev, sr in rescored if sr.gate.status == "fail"),
                "deep_analyzed": sum(1 for ev, _ in rescored if ev.stage_reached == "deep"),
                "ranked": len(passed),
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
