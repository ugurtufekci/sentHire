"""Intake status + parsed candidates per job (docs/10 §3)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from senthire.api.deps import get_db, get_org, parse_uuid, require_admin
from senthire.db.models import (
    Application,
    Candidate,
    CandidateProfileRow,
    Document,
    Job,
    Organization,
    User,
)
from senthire.services import storage

router = APIRouter(tags=["candidates"])


@router.get("/jobs/{job_id}/candidates")
def job_candidates(
    job_id: str,
    org: Organization = Depends(get_org),
    session: Session = Depends(get_db),
) -> dict:
    job = session.get(Job, parse_uuid(job_id, "job_id"))
    if job is None or job.org_id != org.id:
        raise HTTPException(status_code=404, detail="job not found")

    docs = session.scalars(
        select(Document)
        .where(Document.org_id == org.id, Document.upload_job_id == job.id)
        .order_by(Document.created_at)
    ).all()

    apps = session.scalars(select(Application).where(Application.job_id == job.id)).all()
    candidates = {
        c.id: c
        for c in session.scalars(
            select(Candidate).where(Candidate.id.in_([a.candidate_id for a in apps]))
        ).all()
    }
    profiles = {
        p.candidate_id: p
        for p in session.scalars(
            select(CandidateProfileRow).where(
                CandidateProfileRow.candidate_id.in_([a.candidate_id for a in apps])
            )
        ).all()
    }

    files = [
        {
            "document_id": str(d.id),
            "filename": d.original_filename,
            "parse_status": d.parse_status,
            "document_kind": d.document_kind,
            "error": (d.parse_error or {}).get("reason") if d.parse_error else None,
        }
        for d in docs
    ]

    applications = []
    for a in apps:
        c = candidates.get(a.candidate_id)
        p = profiles.get(a.candidate_id)
        derived = (p.profile.get("derived", {}) if p else {}) or {}
        applications.append(
            {
                "application_id": str(a.id),
                "status": a.status,
                "candidate": {
                    "id": str(a.candidate_id),
                    "display_name": c.display_name if c else None,
                },
                "profile_summary": {
                    "total_experience_months": derived.get("total_experience_months"),
                    "seniority": derived.get("seniority_estimate"),
                    "city": (p.profile.get("location", {}) or {}).get("city_canonical") if p else None,
                    "extraction_confidence": p.extraction_confidence if p else None,
                }
                if p
                else None,
            }
        )

    counts: dict[str, int] = {}
    for d in docs:
        counts[d.parse_status] = counts.get(d.parse_status, 0) + 1

    return {"job_id": str(job.id), "funnel": counts, "files": files, "applications": applications}


@router.get("/applications/{application_id}/document")
def application_document(
    application_id: str,
    org: Organization = Depends(get_org),
    session: Session = Depends(get_db),
) -> dict:
    """A short-lived URL to the candidate's original CV.

    The evidence quotes are excerpts; the hiring decision is made on the
    document. Presigned in production, served by the API under the local
    backend — the caller cannot tell the difference and should not need to.
    """
    application = session.get(Application, parse_uuid(application_id, "application_id"))
    if application is None or application.org_id != org.id:
        raise HTTPException(status_code=404, detail="application not found")
    document = session.get(Document, application.document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="document not found")
    return {
        "url": storage.presign_get(document.s3_key),
        "filename": document.original_filename,
    }


class EraseIn(BaseModel):
    # The client must send the candidate id back: a bare DELETE from a mis-
    # wired button must not destroy a person's record.
    confirm_candidate_id: str


@router.post("/candidates/{candidate_id}/erase")
def erase_candidate_data(
    candidate_id: str,
    payload: EraseIn,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_db),
) -> dict:
    """KVKK/GDPR erasure — admin-only, irreversible, audited."""
    from senthire.services.erasure import erase_candidate

    parsed = parse_uuid(candidate_id, "candidate_id")
    if payload.confirm_candidate_id != candidate_id:
        raise HTTPException(status_code=422, detail="onay kimliği eşleşmiyor")
    candidate = session.get(Candidate, parsed)
    if candidate is None or candidate.org_id != admin.org_id:
        raise HTTPException(status_code=404, detail="candidate not found")
    if candidate.erased_at is not None:
        return {"already_erased": True}
    result = erase_candidate(session, candidate, actor=admin)
    session.commit()
    return result
