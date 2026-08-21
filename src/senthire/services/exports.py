"""CSV exports: the ranking and the pipeline, in files Turkish Excel opens.

The point of an export is the meeting where screening results get argued about,
and that meeting happens in Excel. Two details decide whether the file works
there or arrives as one garbled column:

- **UTF-8 BOM.** Without it, Excel guesses Windows-1254 and every İ/ş/ğ breaks.
- **Semicolon separator.** Turkish locale uses the comma as the decimal mark,
  so Turkish Excel splits on ";" — a comma-separated file lands in one column.

Scores are written with a decimal comma for the same reason. These files are
built for the humans who will actually open them, not for a parser.
"""

import csv
import io
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from senthire.db.models import (
    Application,
    Candidate,
    CandidateMessage,
    Evaluation,
    Job,
    PipelineEvent,
    ScreeningRun,
    User,
)

SEPARATOR = ";"
BOM = "﻿"

BAND_TR = {
    "top": "En iyi", "strong": "Güçlü", "possible": "Olası",
    "weak": "Zayıf", "rejected": "Elendi",
}
STAGE_TR = {
    "new": "Yeni", "shortlisted": "Kısa liste", "contacted": "Temas kuruldu",
    "interviewing": "Görüşme", "offer": "Teklif", "hired": "İşe alındı",
    "dropped": "Olumsuz",
}
VERDICT_TR = {
    "met": "Karşılıyor", "partially_met": "Kısmen", "not_met": "Karşılamıyor",
    "unknown": "Bilgi yok", "disqualified": "Elendi",
}


def _score(value: float | None) -> str:
    return "" if value is None else f"{value:.1f}".replace(".", ",")


def _date(value: datetime | None) -> str:
    return value.strftime("%d.%m.%Y %H:%M") if value else ""


def _csv(header: list[str], rows: list[list[str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=SEPARATOR, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(header)
    writer.writerows(rows)
    return BOM + buffer.getvalue()


def run_results_csv(session: Session, run: ScreeningRun) -> tuple[str, str]:
    """(filename, content): the full ranking, one row per candidate, one column
    per requirement — the table HR would otherwise rebuild by hand."""
    job = session.get(Job, run.job_id)
    evaluations = session.scalars(
        select(Evaluation).where(Evaluation.run_id == run.id)
    ).all()
    applications = {
        a.id: a
        for a in session.scalars(
            select(Application).where(Application.id.in_([e.application_id for e in evaluations]))
        )
    }
    candidates = {
        c.id: c
        for c in session.scalars(
            select(Candidate).where(
                Candidate.id.in_([a.candidate_id for a in applications.values()])
            )
        )
    }

    # Requirement columns in spec order, from whichever evaluation has them.
    requirement_ids: list[str] = []
    requirement_labels: dict[str, str] = {}
    for evaluation in evaluations:
        for row in (evaluation.result or {}).get("requirements", []):
            if row["req_id"] not in requirement_labels:
                requirement_ids.append(row["req_id"])
                labels = row.get("label") or {}
                requirement_labels[row["req_id"]] = labels.get("tr") or row["req_id"]

    header = [
        "Sıra", "Aday", "E-posta", "Puan", "Seviye", "Ön filtre", "İnceleme",
        *[requirement_labels[r] for r in requirement_ids],
        "Elenme nedeni",
    ]

    def sort_key(evaluation: Evaluation):
        return (evaluation.rank is None, evaluation.rank or 0, -(evaluation.overall_score or 0))

    rows = []
    for evaluation in sorted(evaluations, key=sort_key):
        application = applications.get(evaluation.application_id)
        candidate = candidates.get(application.candidate_id) if application else None
        result = evaluation.result or {}
        verdicts = {r["req_id"]: r for r in result.get("requirements", [])}
        rejection = "; ".join(
            (r.get("label") or r["req_id"])
            for r in (result.get("rejection_reasons") or [])
            if isinstance(r.get("label"), str) or r.get("req_id")
        ) if result.get("rejection_reasons") else ""
        rows.append(
            [
                str(evaluation.rank) if evaluation.rank else "—",
                (candidate.display_name if candidate else None) or "İsimsiz aday",
                (candidate.primary_email if candidate else None) or "",
                _score(evaluation.overall_score),
                BAND_TR.get(evaluation.band or "", evaluation.band or ""),
                "Geçti" if evaluation.hard_result == "pass" else "Elendi",
                "Önerilir" if result.get("needs_review") else "",
                *[
                    VERDICT_TR.get(verdicts.get(r, {}).get("verdict", ""), "")
                    for r in requirement_ids
                ],
                rejection,
            ]
        )
    stamp = (run.finished_at or run.started_at or datetime.now()).strftime("%Y%m%d")
    title = (job.title if job else "tarama").replace("/", "-")[:60]
    return f"siralama-{title}-{stamp}.csv", _csv(header, rows)


def pipeline_csv(session: Session, job: Job) -> tuple[str, str]:
    """(filename, content): where every candidate stands, with owner, next step,
    last contact — the status report managers ask for weekly."""
    applications = session.scalars(
        select(Application).where(Application.job_id == job.id)
    ).all()
    candidates = {
        c.id: c
        for c in session.scalars(
            select(Candidate).where(Candidate.id.in_([a.candidate_id for a in applications]))
        )
    }
    owners = {
        u.id: u
        for u in session.scalars(select(User).where(User.org_id == job.org_id))
    }
    scores = {
        e.application_id: e.overall_score
        for e in session.scalars(
            select(Evaluation)
            .where(Evaluation.application_id.in_([a.id for a in applications]))
            .order_by(Evaluation.created_at)
        )
    }
    last_contacts = {}
    for event in session.scalars(
        select(PipelineEvent)
        .where(
            PipelineEvent.application_id.in_([a.id for a in applications]),
            PipelineEvent.kind == "contact",
        )
        .order_by(PipelineEvent.created_at)
    ):
        last_contacts[event.application_id] = event.created_at
    message_counts: dict = {}
    for message in session.scalars(
        select(CandidateMessage).where(
            CandidateMessage.application_id.in_([a.id for a in applications])
        )
    ):
        message_counts[message.application_id] = message_counts.get(message.application_id, 0) + 1

    header = [
        "Aday", "E-posta", "Puan", "Aşama", "Aşama tarihi", "Sorumlu",
        "Sonraki adım", "Adım tarihi", "Son temas", "Gönderilen mesaj",
    ]
    stage_order = list(STAGE_TR)
    rows = []
    for application in sorted(
        applications,
        key=lambda a: (stage_order.index(a.stage) if a.stage in stage_order else 99,
                       -(scores.get(a.id) or 0)),
    ):
        candidate = candidates.get(application.candidate_id)
        owner = owners.get(application.owner_id) if application.owner_id else None
        rows.append(
            [
                (candidate.display_name if candidate else None) or "İsimsiz aday",
                (candidate.primary_email if candidate else None) or "",
                _score(scores.get(application.id)),
                STAGE_TR.get(application.stage, application.stage),
                _date(application.stage_changed_at),
                (owner.name or owner.email) if owner else "",
                application.next_action or "",
                _date(application.next_action_at),
                _date(last_contacts.get(application.id)),
                str(message_counts.get(application.id, 0)),
            ]
        )
    title = job.title.replace("/", "-")[:60]
    stamp = datetime.now().strftime("%Y%m%d")
    return f"aday-akisi-{title}-{stamp}.csv", _csv(header, rows)
