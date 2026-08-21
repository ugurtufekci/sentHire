"""KVKK/GDPR erasure: remove one candidate's personal data, provably.

The request this answers is a legal one — "delete my data" — and the answer
has to hold up when a lawyer asks what remains. The rule: everything that can
identify the person goes; the *counts* may stay. A workspace keeps knowing it
screened 40 people in August; it stops knowing that one of them was Deniz.

What goes, and why each item is on the list:

- documents in object storage and their parsed profiles + raw text — the CV is
  personal data in its entirety;
- evaluation result documents — evidence quotes are verbatim CV excerpts;
- pipeline events and candidate messages — notes and letters name the person;
- the candidate row's own fields — name, e-mail, phone, identity keys.

The candidate row itself survives as an anonymized stub with `erased_at` set:
foreign keys stay valid, aggregate statistics stay honest, and the partial
unique index (0009) ignores erased rows, so the same person can apply again
tomorrow as a brand-new candidate — erasure must not become a ban.

An audit line records that an erasure happened and who asked for it, carrying
no personal data itself. "We deleted it" needs a timestamp to be believable.
"""

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from senthire.db.models import (
    Application,
    AuditLog,
    Candidate,
    CandidateMessage,
    CandidateProfileRow,
    Document,
    Evaluation,
    Override,
    PipelineEvent,
    RequirementResult,
    User,
)
from senthire.services import storage


def erase_candidate(session: Session, candidate: Candidate, *, actor: User) -> dict:
    """Erase one candidate across the workspace. Returns what was removed."""
    applications = session.scalars(
        select(Application).where(Application.candidate_id == candidate.id)
    ).all()
    application_ids = [a.id for a in applications]

    documents = session.scalars(
        select(Document).where(Document.candidate_id == candidate.id)
    ).all()
    storage_failures = 0
    for document in documents:
        try:
            storage.delete_object(document.s3_key)
        except Exception:
            # The DB rows still go; a lingering blob is a follow-up, not a
            # reason to refuse the erasure.
            storage_failures += 1

    counts = {"applications": len(application_ids), "documents": len(documents)}

    if application_ids:
        for model in (Override, PipelineEvent, CandidateMessage):
            session.execute(
                delete(model).where(model.application_id.in_(application_ids))
            )
        # Evaluations stay as rows (run funnel counts must not silently change)
        # but everything readable in them goes — including the per-requirement
        # rows, whose evidence column quotes the CV verbatim.
        evaluation_ids = list(
            session.scalars(
                select(Evaluation.id).where(
                    Evaluation.application_id.in_(application_ids)
                )
            )
        )
        if evaluation_ids:
            session.execute(
                delete(RequirementResult).where(
                    RequirementResult.evaluation_id.in_(evaluation_ids)
                )
            )
        for evaluation in session.scalars(
            select(Evaluation).where(Evaluation.application_id.in_(application_ids))
        ):
            evaluation.result = {"erased": True}
        for application in applications:
            application.next_action = None
            application.next_action_at = None
            application.owner_id = None

    session.execute(
        delete(CandidateProfileRow).where(CandidateProfileRow.candidate_id == candidate.id)
    )
    for document in documents:
        document.original_filename = None
        document.s3_key = f"erased/{document.id}"
        document.parse_status = "failed"
        document.parse_error = {"reason": "erased"}

    candidate.display_name = None
    candidate.primary_email = None
    candidate.primary_phone = None
    candidate.identity_keys = []
    candidate.erased_at = datetime.now(UTC)

    session.add(
        AuditLog(
            org_id=candidate.org_id,
            actor=actor.id,
            event="candidate.erased",
            entity={"type": "candidate", "id": str(candidate.id)},
            detail={**counts, "storage_failures": storage_failures},
        )
    )
    return {**counts, "storage_failures": storage_failures}
