"""Screening runs: start, watch the funnel, read ranked results (docs/01 §3–4)."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from senthire.api.deps import get_current_user, get_db, get_org, parse_uuid
from senthire.api.enqueue import enqueue_after_commit
from senthire.config import get_settings
from senthire.db.models import (
    Application,
    Candidate,
    Evaluation,
    EvaluationSpecRow,
    Job,
    Organization,
    ScreeningRun,
    User,
)
from senthire.domain.ranking import equivalence_groups
from senthire.domain.spec import EvaluationSpec
from senthire.services import exports
from senthire.services import overrides as override_service

router = APIRouter(tags=["runs"])


class VerdictCorrection(BaseModel):
    """HR disagreeing with one requirement's verdict."""

    verdict: Literal["met", "partially_met", "not_met", "unknown"]
    reason: str | None = Field(default=None, max_length=1000)


class RunCreate(BaseModel):
    # interactive = results in minutes at list price;
    # batch = Message Batches transport, LLM tokens at 50% (docs/07 §5)
    mode: Literal["interactive", "batch"] = "interactive"


@router.post("/jobs/{job_id}/runs", status_code=202)
def start_run(
    job_id: str,
    payload: RunCreate,
    org: Organization = Depends(get_org),
    session: Session = Depends(get_db),
) -> dict:
    job = session.get(Job, parse_uuid(job_id, "job_id"))
    if job is None or job.org_id != org.id:
        raise HTTPException(status_code=404, detail="job not found")

    spec_row = session.scalar(
        select(EvaluationSpecRow)
        .where(EvaluationSpecRow.job_id == job.id, EvaluationSpecRow.status == "confirmed")
        .order_by(EvaluationSpecRow.version.desc())
    )
    if spec_row is None:
        raise HTTPException(status_code=409, detail="job has no confirmed requirement spec")

    profiled = session.scalar(
        select(func.count())
        .select_from(Application)
        .where(Application.job_id == job.id, Application.status != "received")
    )
    if not profiled:
        raise HTTPException(status_code=409, detail="no parsed candidates to screen")

    run = ScreeningRun(org_id=org.id, job_id=job.id, spec_id=spec_row.id, mode=payload.mode)
    if get_settings().fake_models:
        # Stamped at the source. A demo result that cannot be told apart from a
        # real screening is a liability, so the run carries the mark and the UI
        # repeats it.
        run.funnel = {"fake_models": True}
    session.add(run)
    session.flush()

    from senthire.workers.tasks.screen import run_start

    enqueue_after_commit(session, run_start, str(run.id))
    return {"run_id": str(run.id), "status": "queued", "spec_version": spec_row.version}


@router.get("/jobs/{job_id}/runs")
def list_runs(
    job_id: str,
    org: Organization = Depends(get_org),
    session: Session = Depends(get_db),
) -> list[dict]:
    job = session.get(Job, parse_uuid(job_id, "job_id"))
    if job is None or job.org_id != org.id:
        raise HTTPException(status_code=404, detail="job not found")
    runs = session.scalars(
        select(ScreeningRun)
        .where(ScreeningRun.job_id == job.id)
        .order_by(ScreeningRun.started_at.desc().nulls_last())
    ).all()
    return [
        {
            "run_id": str(r.id),
            "status": r.status,
            "mode": r.mode,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "funnel": r.funnel or {},
        }
        for r in runs
    ]


def _get_run(run_id: str, org: Organization, session: Session) -> ScreeningRun:
    run = session.get(ScreeningRun, parse_uuid(run_id, "run_id"))
    if run is None or run.org_id != org.id:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@router.get("/runs/{run_id}")
def run_status(
    run_id: str,
    org: Organization = Depends(get_org),
    session: Session = Depends(get_db),
) -> dict:
    run = _get_run(run_id, org, session)
    by_stage = dict(
        session.execute(
            select(Evaluation.stage_reached, func.count())
            .where(Evaluation.run_id == run.id)
            .group_by(Evaluation.stage_reached)
        ).all()
    )
    funnel = dict(run.funnel or {})
    funnel["by_stage"] = by_stage
    funnel["evaluated_so_far"] = sum(by_stage.values())
    return {
        "run_id": str(run.id),
        "job_id": str(run.job_id),
        "status": run.status,
        "mode": run.mode,
        "funnel": funnel,
        "cost": run.cost or {},
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def _headline(result: dict) -> dict:
    narrative = result.get("narrative") or {}
    return {
        "strengths": (narrative.get("strengths") or [])[:3],
        "weaknesses": (narrative.get("weaknesses") or [])[:3],
        "summary": narrative.get("summary"),
    }


@router.get("/runs/{run_id}/results")
def run_results(
    run_id: str,
    include_rejected: bool = Query(default=True),
    org: Organization = Depends(get_org),
    session: Session = Depends(get_db),
) -> dict:
    run = _get_run(run_id, org, session)
    evaluations = session.scalars(
        select(Evaluation).where(Evaluation.run_id == run.id)
    ).all()
    candidates = {
        c.id: c
        for c in session.scalars(
            select(Candidate)
            .join(Application, Application.candidate_id == Candidate.id)
            .where(Application.id.in_([e.application_id for e in evaluations]))
        ).all()
    }
    apps = {
        a.id: a
        for a in session.scalars(
            select(Application).where(Application.id.in_([e.application_id for e in evaluations]))
        ).all()
    }

    def row(ev: Evaluation) -> dict:
        app = apps.get(ev.application_id)
        cand = candidates.get(app.candidate_id) if app else None
        return {
            "application_id": str(ev.application_id),
            "candidate": {
                "id": str(app.candidate_id) if app else None,
                "display_name": cand.display_name if cand else None,
            },
            "rank": ev.rank,
            "overall_score": ev.overall_score,
            "band": ev.band,
            "hard_result": ev.hard_result,
            "confidence": ev.confidence,
            "stage_reached": ev.stage_reached,
            "needs_review": bool((ev.result or {}).get("needs_review")),
            "headline": _headline(ev.result or {}),
        }

    ranked = sorted(
        (ev for ev in evaluations if ev.rank is not None), key=lambda e: e.rank
    )
    rejected = [ev for ev in evaluations if ev.rank is None]
    groups = equivalence_groups([e.overall_score for e in ranked])
    out = {
        "run_id": str(run.id),
        "status": run.status,
        "spec_version": next((e.spec_version for e in evaluations), None),
        "results": [
            {**row(e), "equivalent_group": group}
            for e, group in zip(ranked, groups, strict=True)
        ],
    }
    if include_rejected:
        out["rejected"] = [
            {**row(e), "rejection_reasons": (e.result or {}).get("rejection_reasons")}
            for e in rejected
        ]
    return out


@router.get("/runs/{run_id}/results/{application_id}")
def run_result_detail(
    run_id: str,
    application_id: str,
    org: Organization = Depends(get_org),
    session: Session = Depends(get_db),
) -> dict:
    run = _get_run(run_id, org, session)
    ev = session.scalar(
        select(Evaluation).where(
            Evaluation.run_id == run.id,
            Evaluation.application_id == uuid.UUID(application_id),
        )
    )
    if ev is None:
        raise HTTPException(status_code=404, detail="evaluation not found")
    return {
        "application_id": str(ev.application_id),
        "rank": ev.rank,
        "overall_score": ev.overall_score,
        "band": ev.band,
        "hard_result": ev.hard_result,
        "confidence": ev.confidence,
        "stage_reached": ev.stage_reached,
        "profile_version": ev.profile_version,
        "spec_version": ev.spec_version,
        "pipeline_version": ev.pipeline_version,
        "models_used": ev.models_used,
        "result": ev.result,
    }


def _evaluation(run: ScreeningRun, application_id: str, session: Session) -> Evaluation:
    evaluation = session.scalar(
        select(Evaluation).where(
            Evaluation.run_id == run.id,
            Evaluation.application_id == parse_uuid(application_id, "application_id"),
        )
    )
    if evaluation is None:
        raise HTTPException(status_code=404, detail="evaluation not found")
    return evaluation


@router.post("/runs/{run_id}/results/{application_id}/requirements/{req_id}/override")
def override_verdict(
    run_id: str,
    application_id: str,
    req_id: str,
    payload: VerdictCorrection,
    user: User = Depends(get_current_user),
    org: Organization = Depends(get_org),
    session: Session = Depends(get_db),
) -> dict:
    """Correct one verdict. The score, gate and ranking follow deterministically."""
    run = _get_run(run_id, org, session)
    evaluation = _evaluation(run, application_id, session)
    spec_row = session.get(EvaluationSpecRow, run.spec_id)
    spec = EvaluationSpec.model_validate(spec_row.spec)
    try:
        override_service.correct_verdict(
            session,
            evaluation=evaluation,
            spec=spec,
            req_id=req_id,
            verdict=payload.verdict,
            reason=payload.reason,
            user=user,
        )
    except override_service.OverrideError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return run_result_detail(run_id, application_id, org, session)


@router.get("/runs/{run_id}/results.csv")
def run_results_csv(
    run_id: str,
    org: Organization = Depends(get_org),
    session: Session = Depends(get_db),
):
    """The ranking as a file Turkish Excel opens correctly (BOM + semicolons)."""
    from urllib.parse import quote

    from fastapi.responses import Response

    run = _get_run(run_id, org, session)
    filename, content = exports.run_results_csv(session, run)
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
        },
    )
