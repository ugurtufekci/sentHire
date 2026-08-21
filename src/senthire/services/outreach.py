"""Writing to candidates: templates, previews, and the outbox.

Screening produces a shortlist and then someone has to actually write to those
people. Doing it outside the product means the candidate's timeline stops at
"shortlisted" and nobody can answer "did we ever reply to this person?" — which
in Turkish hiring is the complaint candidates make most.

Three rules shape everything here, and they are all about not doing harm at
scale:

1. **Nothing sends itself.** A stage change never triggers an email. One
   mis-drag would write to a real person in a way that cannot be recalled, and
   an "automatic outreach" feature is exactly how a screening tool starts
   spamming applicants. A human presses send, having seen the text.
2. **What was sent is what was shown.** The outbox stores rendered copy, not a
   template id, so editing a template tomorrow cannot rewrite what a candidate
   received.
3. **The candidate can answer a person.** Mail leaves from the platform address
   with the recruiter's address as Reply-To; a no-reply invitation to a job
   interview is a discourtesy.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from senthire.db.models import (
    Application,
    AuditLog,
    Candidate,
    CandidateMessage,
    Job,
    MessageTemplate,
    Organization,
    PipelineEvent,
    User,
)
from senthire.services import calendar
from senthire.services.email import render_plain_email
from senthire.workers.tasks.mail import enqueue_mail

# Deliberately few, and all derivable without asking the recruiter to type
# anything twice. A variable nobody can fill is a template nobody can use.
VARIABLES = ("aday", "ilan", "sirket", "gonderen", "tarih")
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")

MAX_RECIPIENTS = 50

# Which stage a message implies. Sending an invitation and leaving the board
# untouched would make the board lie within the hour.
IMPLIED_STAGE = {"interview_invite": "contacted", "rejection": "dropped"}

DEFAULT_TEMPLATES: list[dict] = [
    {
        "slug": "interview_invite",
        "name": "Mülakat daveti",
        "subject": "{{ilan}} pozisyonu için görüşme daveti — {{sirket}}",
        "body": """Merhaba {{aday}},

{{sirket}} olarak {{ilan}} pozisyonu için yaptığınız başvuruyu değerlendirdik ve sizinle tanışmak isteriz.

Görüşme için önerdiğimiz zaman: {{tarih}}

Bu zaman size uymuyorsa, uygun olduğunuz aralıkları bu e-postayı yanıtlayarak iletebilirsiniz; birlikte ayarlarız.

İyi çalışmalar,
{{gonderen}}
{{sirket}}""",
    },
    {
        "slug": "rejection",
        "name": "Olumsuz yanıt",
        "subject": "{{ilan}} başvurunuz hakkında — {{sirket}}",
        "body": """Merhaba {{aday}},

{{sirket}} olarak {{ilan}} pozisyonu için ayırdığınız zaman ve gösterdiğiniz ilgi için teşekkür ederiz.

Değerlendirmemiz sonucunda bu pozisyonda diğer adaylarla devam etme kararı aldık. Bu karar, deneyiminizin değerli olmadığı anlamına gelmiyor; yalnızca bu ilanın gereksinimleriyle örtüşme derecesine dair bir tercih.

Başvurunuzu ileriye dönük olarak kayıtlarımızda tutmamızı isterseniz bu e-postayı yanıtlamanız yeterli.

Başarılar dileriz,
{{gonderen}}
{{sirket}}""",
    },
    {
        "slug": "info_request",
        "name": "Ek bilgi talebi",
        "subject": "{{ilan}} başvurunuz için birkaç soru — {{sirket}}",
        "body": """Merhaba {{aday}},

{{ilan}} pozisyonu için yaptığınız başvuruyu inceliyoruz. Değerlendirmeyi sağlıklı yapabilmek için CV'nizde göremediğimiz birkaç konuyu sormak istiyoruz:

- (soru 1)
- (soru 2)

Bu e-postayı yanıtlayarak iletebilirsiniz.

Teşekkürler,
{{gonderen}}
{{sirket}}""",
    },
]


class OutreachError(ValueError):
    pass


@dataclass
class Rendered:
    subject: str
    body: str
    to_email: str | None
    candidate_name: str | None
    application_id: str
    blocked: str | None = None  # why this candidate cannot be written to


def templates_for(session: Session, org_id) -> list[MessageTemplate]:
    """The workspace's templates, seeding the defaults the first time."""
    existing = {
        row.slug: row
        for row in session.scalars(
            select(MessageTemplate).where(MessageTemplate.org_id == org_id)
        )
    }
    created = False
    for default in DEFAULT_TEMPLATES:
        if default["slug"] in existing:
            continue
        row = MessageTemplate(org_id=org_id, **default)
        session.add(row)
        existing[default["slug"]] = row
        created = True
    if created:
        session.flush()
    order = [d["slug"] for d in DEFAULT_TEMPLATES]
    return sorted(existing.values(), key=lambda t: order.index(t.slug) if t.slug in order else 99)


def render(text: str, context: dict[str, str]) -> str:
    """Substitute {{variables}}, refusing any the workspace cannot fill.

    A silently-empty placeholder is how "Merhaba ," reaches a candidate.
    """
    unknown = {name for name in _PLACEHOLDER.findall(text) if name not in VARIABLES}
    if unknown:
        raise OutreachError(
            f"bilinmeyen alan: {', '.join(sorted(unknown))} — kullanılabilir: "
            + ", ".join(f"{{{{{v}}}}}" for v in VARIABLES)
        )
    return _PLACEHOLDER.sub(lambda m: context.get(m.group(1), ""), text)


def context_for(
    session: Session, application: Application, sender: User, *, when: str | None
) -> dict[str, str]:
    candidate = session.get(Candidate, application.candidate_id)
    job = session.get(Job, application.job_id)
    org = session.get(Organization, application.org_id)
    return {
        "aday": (candidate.display_name if candidate else None) or "Değerli aday",
        "ilan": job.title if job else "",
        "sirket": org.name if org else "",
        "gonderen": sender.name or sender.email,
        "tarih": when or _default_when(application),
    }


def _default_when(application: Application) -> str:
    """The meeting already on the candidate's card, if there is one."""
    if application.next_action_at:
        return application.next_action_at.astimezone(UTC).strftime("%d.%m.%Y %H:%M")
    return "(görüşme zamanı)"


def preview(
    session: Session,
    applications: list[Application],
    *,
    subject: str,
    body: str,
    sender: User,
    when: str | None = None,
) -> list[Rendered]:
    """Render for every recipient, marking the ones that cannot be written to."""
    out: list[Rendered] = []
    for application in applications:
        candidate = session.get(Candidate, application.candidate_id)
        context = context_for(session, application, sender, when=when)
        out.append(
            Rendered(
                subject=render(subject, context),
                body=render(body, context),
                to_email=candidate.primary_email if candidate else None,
                candidate_name=candidate.display_name if candidate else None,
                application_id=str(application.id),
                blocked=None
                if (candidate and candidate.primary_email)
                else "CV'de e-posta adresi yok",
            )
        )
    return out


def already_sent(session: Session, application_id, template_slug: str | None) -> CandidateMessage | None:
    if template_slug is None:
        return None
    return session.scalars(
        select(CandidateMessage)
        .where(
            CandidateMessage.application_id == application_id,
            CandidateMessage.template_slug == template_slug,
            CandidateMessage.status != "failed",
        )
        .order_by(CandidateMessage.created_at.desc())
        .limit(1)
    ).first()


def send(
    session: Session,
    application: Application,
    rendered: Rendered,
    *,
    sender: User,
    template_slug: str | None,
    advance_stage: bool = True,
    when: str | None = None,
) -> CandidateMessage:
    """Queue one message, record it, and move the card if the message implies it."""
    if rendered.blocked or not rendered.to_email:
        raise OutreachError(rendered.blocked or "alıcı adresi yok")

    message = CandidateMessage(
        org_id=application.org_id,
        application_id=application.id,
        template_slug=template_slug,
        to_email=rendered.to_email,
        subject=rendered.subject,
        body=rendered.body,
        created_by=sender.id,
    )
    session.add(message)
    session.flush()

    html, text = render_plain_email(rendered.subject, rendered.body)
    # An interview invitation with an unambiguous time carries a calendar
    # invite: the meeting lands in the candidate's calendar with one tap, and
    # Accept/Decline comes back to the recruiter's own mailbox via Reply-To.
    ics = None
    starts_at = calendar.parse_when(when) if template_slug == "interview_invite" else None
    if starts_at is not None:
        job = session.get(Job, application.job_id)
        org = session.get(Organization, application.org_id)
        ics = calendar.interview_ics(
            summary=f"Görüşme — {job.title if job else ''} ({org.name if org else ''})",
            starts_at=starts_at,
            organizer_name=sender.name or sender.email,
            organizer_email=sender.email,
            attendee_email=rendered.to_email,
            description=rendered.body,
            uid=str(message.id),
        )
    queued = enqueue_mail(
        rendered.to_email, rendered.subject, html, text, reply_to=sender.email, ics=ics
    )
    message.status = "queued" if queued else "failed"
    message.error = None if queued else "e-posta kuyruğuna alınamadı"
    if queued:
        message.sent_at = datetime.now(UTC)

    session.add(
        PipelineEvent(
            org_id=application.org_id,
            application_id=application.id,
            kind="contact",
            actor_id=sender.id,
            note=rendered.subject,
            detail={"channel": "email", "template": template_slug, "to": rendered.to_email},
        )
    )
    session.add(
        AuditLog(
            org_id=application.org_id,
            actor=sender.id,
            event="candidate.message_sent",
            entity={"type": "application", "id": str(application.id)},
            detail={"template": template_slug, "status": message.status},
        )
    )

    target = IMPLIED_STAGE.get(template_slug or "")
    if advance_stage and target and application.stage != target:
        session.add(
            PipelineEvent(
                org_id=application.org_id,
                application_id=application.id,
                kind="stage_change",
                actor_id=sender.id,
                from_stage=application.stage,
                to_stage=target,
                note="e-posta gönderildi",
            )
        )
        application.stage = target
        application.stage_changed_at = datetime.now(UTC)
    return message


def history(session: Session, application_id) -> list[CandidateMessage]:
    return list(
        session.scalars(
            select(CandidateMessage)
            .where(CandidateMessage.application_id == application_id)
            .order_by(CandidateMessage.created_at.desc())
        )
    )
