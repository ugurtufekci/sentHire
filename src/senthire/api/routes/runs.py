"""Screening runs: start, watch the funnel, read ranked results (docs/01 §3–4)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from senthire.api.deps import get_db, get_org, parse_uuid
from senthire.db.models import (
    Application,
    Candidate,
    Evaluation,
    EvaluationSpecRow,
    Job,
    Organization,
    ScreeningRun,
)

router = APIRouter(tags=["runs"])


class RunCreate(BaseModel):
    mode: str = "interactive"  # batch transport lands in a later milestone


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

    if payload.mode != "interactive":
        raise HTTPException(status_code=422, detail="only interactive mode is available yet")

    run = ScreeningRun(org_id=org.id, job_id=job.id, spec_id=spec_row.id, mode=payload.mode)
    session.add(run)
    session.flush()

    from senthire.workers.tasks.screen import run_start

    run_start.delay(str(run.id))
    return {"run_id": str(run.id), "status": "queued", "spec_version": spec_row.version}


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
    out = {
        "run_id": str(run.id),
        "status": run.status,
        "spec_version": next((e.spec_version for e in evaluations), None),
        "results": [row(e) for e in ranked],
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
