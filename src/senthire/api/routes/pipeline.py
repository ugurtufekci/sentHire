"""Hiring pipeline: the board, stage moves, and the candidate timeline.

Screening ends with a ranked list; hiring continues for weeks. This router
tracks the human process — who was shortlisted, contacted, interviewed — as a
kanban board per job. `applications.stage` is the denormalized current column;
every change is also appended to `pipeline_events`, which is the history of
record and is never edited.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from senthire.api.deps import get_current_user, get_db, parse_uuid
from senthire.db.models import (
    Application,
    Candidate,
    Evaluation,
    EvaluationSpecRow,
    Job,
    PipelineEvent,
    ScreeningRun,
    User,
)
from senthire.domain.spec import EvaluationSpec
from senthire.services import exports
from senthire.services import insights as insight_service

router = APIRouter(tags=["pipeline"])

# Board columns, in display order. "new" is the tray of screened-but-untouched
# candidates, not a column; "hired" and "dropped" are terminal but stay
# draggable — people change their minds.
STAGES = ["new", "shortlisted", "contacted", "interviewing", "offer", "hired", "dropped"]
BOARD_STAGES = STAGES[1:]

EVENT_KINDS = {"note", "contact", "meeting", "outcome"}


class StageMove(BaseModel):
    stage: str
    note: str | None = Field(default=None, max_length=2000)


class ApplicationPatch(BaseModel):
    """Partial update; a field explicitly sent as null clears it."""

    owner_id: str | None = None
    next_action: str | None = Field(default=None, max_length=500)
    next_action_at: datetime | None = None


class EventIn(BaseModel):
    kind: str
    note: str | None = Field(default=None, max_length=2000)
    occurs_at: datetime | None = None
    # Free-form extras; "contact" and "outcome" use {"result": "positive"|"negative"}.
    detail: dict = Field(default_factory=dict)


class ShortlistIn(BaseModel):
    application_ids: list[str] = Field(min_length=1, max_length=500)


def _get_application(application_id: str, user: User, session: Session) -> Application:
    app = session.get(Application, parse_uuid(application_id, "application_id"))
    if app is None or app.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="application not found")
    return app


def _latest_evaluations(session: Session, application_ids: list[uuid.UUID]) -> dict:
    """Most recent evaluation per application (later runs supersede earlier)."""
    if not application_ids:
        return {}
    rows = session.scalars(
        select(Evaluation)
        .where(Evaluation.application_id.in_(application_ids))
        .order_by(Evaluation.created_at)
    ).all()
    return {ev.application_id: ev for ev in rows}


def _card(
    app: Application,
    candidate: Candidate | None,
    evaluation: Evaluation | None,
    owner: User | None,
) -> dict:
    return {
        "application_id": str(app.id),
        "candidate_name": candidate.display_name if candidate else None,
        "candidate_email": candidate.primary_email if candidate else None,
        "stage": app.stage,
        "stage_changed_at": app.stage_changed_at.isoformat() if app.stage_changed_at else None,
        "owner_id": str(app.owner_id) if app.owner_id else None,
        "owner_name": (owner.name or owner.email) if owner else None,
        "next_action": app.next_action,
        "next_action_at": app.next_action_at.isoformat() if app.next_action_at else None,
        "score": evaluation.overall_score if evaluation else None,
        "band": evaluation.band if evaluation else None,
        "rank": evaluation.rank if evaluation else None,
    }


def _record_stage_change(
    session: Session, app: Application, to_stage: str, actor: User, note: str | None = None
) -> None:
    session.add(
        PipelineEvent(
            org_id=app.org_id,
            application_id=app.id,
            kind="stage_change",
            actor_id=actor.id,
            from_stage=app.stage,
            to_stage=to_stage,
            note=note,
        )
    )
    app.stage = to_stage
    app.stage_changed_at = datetime.now(UTC)


@router.get("/jobs/{job_id}/pipeline")
def pipeline_board(
    job_id: str,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict:
    job = session.get(Job, parse_uuid(job_id, "job_id"))
    if job is None or job.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="job not found")

    apps = session.scalars(
        select(Application).where(Application.job_id == job.id)
    ).all()
    candidates = {
        c.id: c
        for c in session.scalars(
            select(Candidate).where(Candidate.id.in_([a.candidate_id for a in apps]))
        ).all()
    }
    owners = {
        u.id: u
        for u in session.scalars(select(User).where(User.org_id == user.org_id)).all()
    }
    evaluations = _latest_evaluations(session, [a.id for a in apps])

    def card(app: Application) -> dict:
        return _card(
            app,
            candidates.get(app.candidate_id),
            evaluations.get(app.id),
            owners.get(app.owner_id) if app.owner_id else None,
        )

    # The tray: screened candidates nobody has touched yet, best first. Only
    # ranked ones appear — hard-gate rejects are not board material.
    tray = sorted(
        (
            card(a)
            for a in apps
            if a.stage == "new" and evaluations.get(a.id) is not None
            and evaluations[a.id].rank is not None
        ),
        key=lambda c: -(c["score"] or 0.0),
    )
    columns: dict[str, list[dict]] = {stage: [] for stage in BOARD_STAGES}
    for app in apps:
        if app.stage in columns:
            columns[app.stage].append(card(app))
    for stage_cards in columns.values():
        stage_cards.sort(key=lambda c: (c["next_action_at"] is None, c["next_action_at"] or ""))

    return {
        "job_id": str(job.id),
        "job_title": job.title,
        "stages": BOARD_STAGES,
        "tray": tray,
        "columns": columns,
        "members": [
            {"id": str(u.id), "name": u.name or u.email}
            for u in sorted(owners.values(), key=lambda u: (u.name or u.email).lower())
            if u.is_active
        ],
    }


@router.post("/jobs/{job_id}/pipeline/shortlist")
def shortlist(
    job_id: str,
    payload: ShortlistIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict:
    """Bulk-move tray candidates to the first column. Idempotent per candidate:
    anything already past "new" is left where a human put it."""
    job = session.get(Job, parse_uuid(job_id, "job_id"))
    if job is None or job.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="job not found")

    ids = [parse_uuid(i, "application_id") for i in payload.application_ids]
    apps = session.scalars(
        select(Application).where(Application.id.in_(ids), Application.job_id == job.id)
    ).all()
    moved = 0
    for app in apps:
        if app.stage == "new":
            _record_stage_change(session, app, "shortlisted", user)
            moved += 1
    session.commit()
    return {"moved": moved, "skipped": len(payload.application_ids) - moved}


@router.patch("/applications/{application_id}/stage")
def move_stage(
    application_id: str,
    payload: StageMove,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict:
    if payload.stage not in STAGES:
        raise HTTPException(status_code=422, detail=f"stage must be one of {STAGES}")
    app = _get_application(application_id, user, session)
    if payload.stage != app.stage:
        _record_stage_change(session, app, payload.stage, user, payload.note)
        session.commit()
    return _app_out(app, session)


@router.patch("/applications/{application_id}")
def update_application(
    application_id: str,
    payload: ApplicationPatch,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict:
    app = _get_application(application_id, user, session)
    fields = payload.model_fields_set
    if "owner_id" in fields:
        if payload.owner_id is None:
            app.owner_id = None
        else:
            owner = session.get(User, parse_uuid(payload.owner_id, "owner_id"))
            if owner is None or owner.org_id != user.org_id:
                raise HTTPException(status_code=422, detail="owner must be a workspace member")
            app.owner_id = owner.id
    if "next_action" in fields:
        app.next_action = payload.next_action
    if "next_action_at" in fields:
        app.next_action_at = payload.next_action_at
    if fields:
        session.commit()
    return _app_out(app, session)


@router.post("/applications/{application_id}/events", status_code=201)
def add_event(
    application_id: str,
    payload: EventIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict:
    if payload.kind not in EVENT_KINDS:
        raise HTTPException(status_code=422, detail=f"kind must be one of {sorted(EVENT_KINDS)}")
    app = _get_application(application_id, user, session)
    event = PipelineEvent(
        org_id=app.org_id,
        application_id=app.id,
        kind=payload.kind,
        actor_id=user.id,
        note=payload.note,
        occurs_at=payload.occurs_at,
        detail=payload.detail,
    )
    session.add(event)
    # A scheduled meeting is by definition the next thing owed to this
    # candidate, so it becomes the reminder without a second form.
    if payload.kind == "meeting" and payload.occurs_at is not None:
        app.next_action_at = payload.occurs_at
        if payload.note:
            app.next_action = payload.note[:500]
    session.commit()
    return _event_out(event, user)


@router.get("/applications/{application_id}/timeline")
def timeline(
    application_id: str,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict:
    app = _get_application(application_id, user, session)
    candidate = session.get(Candidate, app.candidate_id)
    owner = session.get(User, app.owner_id) if app.owner_id else None
    evaluation = _latest_evaluations(session, [app.id]).get(app.id)
    events = session.scalars(
        select(PipelineEvent)
        .where(PipelineEvent.application_id == app.id)
        .order_by(PipelineEvent.created_at.desc())
    ).all()
    actors = {
        u.id: u
        for u in session.scalars(select(User).where(User.org_id == user.org_id)).all()
    }
    return {
        **_card(app, candidate, evaluation, owner),
        "job_id": str(app.job_id),
        "candidate_id": str(app.candidate_id),
        "events": [_event_out(e, actors.get(e.actor_id)) for e in events],
    }


def _app_out(app: Application, session: Session) -> dict:
    candidate = session.get(Candidate, app.candidate_id)
    owner = session.get(User, app.owner_id) if app.owner_id else None
    evaluation = _latest_evaluations(session, [app.id]).get(app.id)
    return _card(app, candidate, evaluation, owner)


def _event_out(event: PipelineEvent, actor: User | None) -> dict:
    return {
        "id": str(event.id),
        "kind": event.kind,
        "actor_name": (actor.name or actor.email) if actor else None,
        "from_stage": event.from_stage,
        "to_stage": event.to_stage,
        "note": event.note,
        "occurs_at": event.occurs_at.isoformat() if event.occurs_at else None,
        "detail": event.detail or {},
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


@router.get("/pipeline/agenda")
def agenda(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict:
    """Org-wide "what is owed to whom, when" — the home-page reminder list."""
    apps = session.scalars(
        select(Application)
        .where(
            Application.org_id == user.org_id,
            Application.next_action_at.is_not(None),
        )
        .order_by(Application.next_action_at)
        .limit(20)
    ).all()
    candidates = {
        c.id: c
        for c in session.scalars(
            select(Candidate).where(Candidate.id.in_([a.candidate_id for a in apps]))
        ).all()
    }
    jobs = {
        j.id: j
        for j in session.scalars(
            select(Job).where(Job.id.in_([a.job_id for a in apps]))
        ).all()
    }
    now = datetime.now(UTC)
    return {
        "items": [
            {
                "application_id": str(a.id),
                "job_id": str(a.job_id),
                "job_title": jobs[a.job_id].title if a.job_id in jobs else None,
                "candidate_name": (
                    candidates[a.candidate_id].display_name
                    if a.candidate_id in candidates
                    else None
                ),
                "stage": a.stage,
                "next_action": a.next_action,
                "next_action_at": a.next_action_at.isoformat(),
                "overdue": a.next_action_at < now,
            }
            for a in apps
        ]
    }


@router.get("/jobs/{job_id}/insights")
def job_insights(
    job_id: str,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict:
    """What this job's own corrections and outcomes say about its screening."""
    job = session.get(Job, parse_uuid(job_id, "job_id"))
    if job is None or job.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="job not found")

    spec = None
    run = session.scalars(
        select(ScreeningRun)
        .where(ScreeningRun.job_id == job.id, ScreeningRun.status == "complete")
        .order_by(ScreeningRun.started_at.desc())
        .limit(1)
    ).first()
    if run is not None:
        spec_row = session.get(EvaluationSpecRow, run.spec_id)
        if spec_row is not None:
            spec = EvaluationSpec.model_validate(spec_row.spec)

    corrections = insight_service.correction_patterns(session, job, spec)
    calibration = insight_service.outcome_calibration(session, job)
    return {
        "job_id": str(job.id),
        "corrections": corrections,
        "calibration": calibration,
        "insights": insight_service.summarize(corrections, calibration),
        "min_sample": insight_service.MIN_SAMPLE_FOR_RATE,
    }


@router.get("/jobs/{job_id}/pipeline.csv")
def pipeline_csv(
    job_id: str,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """The weekly status report, without anyone rebuilding it by hand."""
    from urllib.parse import quote

    from fastapi.responses import Response

    job = session.get(Job, parse_uuid(job_id, "job_id"))
    if job is None or job.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="job not found")
    filename, content = exports.pipeline_csv(session, job)
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
        },
    )
