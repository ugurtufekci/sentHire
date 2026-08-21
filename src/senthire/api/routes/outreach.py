"""Candidate-facing email: templates, preview, send, and what was sent.

The send endpoint takes the *rendered* subject and body — the text the
recruiter was looking at — rather than a template id, so there is no gap
between what the screen showed and what the candidate received. Bulk sends
re-render per candidate from the same edited text, because personalising by
hand for twenty people is how people stop personalising at all.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from senthire.api.deps import get_current_user, get_db, parse_uuid
from senthire.db.models import Application, MessageTemplate, User
from senthire.services import outreach

router = APIRouter(tags=["outreach"])


class TemplatePatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    subject: str = Field(max_length=300)
    body: str = Field(max_length=8000)


class PreviewIn(BaseModel):
    application_ids: list[str] = Field(min_length=1, max_length=outreach.MAX_RECIPIENTS)
    subject: str = Field(max_length=300)
    body: str = Field(max_length=8000)
    when: str | None = Field(default=None, max_length=120)


class SendIn(PreviewIn):
    template_slug: str | None = None
    advance_stage: bool = True
    # Writing to the same person with the same template twice is nearly always a
    # mistake, so it takes a deliberate second answer.
    confirm_resend: bool = False


def _template_out(row: MessageTemplate) -> dict:
    return {
        "slug": row.slug,
        "name": row.name,
        "subject": row.subject,
        "body": row.body,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _applications(session: Session, user: User, ids: list[str]) -> list[Application]:
    parsed = [parse_uuid(i, "application_id") for i in ids]
    rows = session.scalars(
        select(Application).where(
            Application.id.in_(parsed), Application.org_id == user.org_id
        )
    ).all()
    if len(rows) != len(set(parsed)):
        raise HTTPException(status_code=404, detail="application not found")
    order = {application_id: index for index, application_id in enumerate(parsed)}
    return sorted(rows, key=lambda a: order[a.id])


@router.get("/messages/templates")
def list_templates(
    user: User = Depends(get_current_user), session: Session = Depends(get_db)
) -> dict:
    rows = outreach.templates_for(session, user.org_id)
    session.commit()
    return {
        "templates": [_template_out(row) for row in rows],
        "variables": list(outreach.VARIABLES),
    }


@router.put("/messages/templates/{slug}")
def update_template(
    slug: str,
    payload: TemplatePatch,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict:
    rows = {row.slug: row for row in outreach.templates_for(session, user.org_id)}
    row = rows.get(slug)
    if row is None:
        raise HTTPException(status_code=404, detail="template not found")
    try:
        outreach.render(payload.subject, dict.fromkeys(outreach.VARIABLES, ""))
        outreach.render(payload.body, dict.fromkeys(outreach.VARIABLES, ""))
    except outreach.OutreachError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    row.subject, row.body = payload.subject, payload.body
    if payload.name:
        row.name = payload.name
    row.updated_by = user.id
    from datetime import UTC, datetime

    row.updated_at = datetime.now(UTC)
    session.commit()
    return _template_out(row)


@router.post("/messages/preview")
def preview_messages(
    payload: PreviewIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict:
    applications = _applications(session, user, payload.application_ids)
    try:
        rendered = outreach.preview(
            session, applications, subject=payload.subject, body=payload.body,
            sender=user, when=payload.when,
        )
    except outreach.OutreachError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()  # templates may have been seeded
    return {
        "messages": [
            {
                "application_id": r.application_id,
                "candidate_name": r.candidate_name,
                "to_email": r.to_email,
                "subject": r.subject,
                "body": r.body,
                "blocked": r.blocked,
            }
            for r in rendered
        ]
    }


@router.post("/messages/send")
def send_messages(
    payload: SendIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict:
    applications = _applications(session, user, payload.application_ids)
    try:
        rendered = outreach.preview(
            session, applications, subject=payload.subject, body=payload.body,
            sender=user, when=payload.when,
        )
    except outreach.OutreachError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    by_id = {str(a.id): a for a in applications}
    sent, skipped = [], []
    for message in rendered:
        application = by_id[message.application_id]
        if message.blocked:
            skipped.append({"application_id": message.application_id, "reason": message.blocked})
            continue
        previous = outreach.already_sent(session, application.id, payload.template_slug)
        if previous is not None and not payload.confirm_resend:
            skipped.append(
                {
                    "application_id": message.application_id,
                    "reason": "bu adaya bu şablon zaten gönderildi",
                    "sent_at": previous.created_at.isoformat() if previous.created_at else None,
                    "needs_confirmation": True,
                }
            )
            continue
        record = outreach.send(
            session,
            application,
            message,
            sender=user,
            template_slug=payload.template_slug,
            advance_stage=payload.advance_stage,
            when=payload.when,
        )
        sent.append(
            {
                "application_id": message.application_id,
                "to_email": record.to_email,
                "status": record.status,
            }
        )
    session.commit()
    from senthire.services.calendar import parse_when

    return {
        "sent": sent,
        "skipped": skipped,
        # so the composer can say "takvim daveti eklendi" truthfully
        "calendar_attached": (
            payload.template_slug == "interview_invite"
            and parse_when(payload.when) is not None
        ),
    }


@router.get("/applications/{application_id}/messages")
def application_messages(
    application_id: str,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict:
    application = session.get(Application, parse_uuid(application_id, "application_id"))
    if application is None or application.org_id != user.org_id:
        raise HTTPException(status_code=404, detail="application not found")
    return {
        "messages": [
            {
                "id": str(m.id),
                "template_slug": m.template_slug,
                "to_email": m.to_email,
                "subject": m.subject,
                "body": m.body,
                "status": m.status,
                "error": m.error,
                "sent_at": m.sent_at.isoformat() if m.sent_at else None,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in outreach.history(session, application.id)
        ]
    }
