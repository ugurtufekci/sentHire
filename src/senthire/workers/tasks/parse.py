"""Stage 0 (intake) + Stage 1 (extraction) worker tasks (docs/02, docs/08).

Idempotency: documents are content-addressed — unique (org_id, sha256) — and
parse state advances through a guarded state machine, so redelivered tasks
no-op instead of double-spending model calls.
"""

import hashlib
import uuid
from datetime import UTC, datetime

import anthropic
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from senthire import PIPELINE_VERSION
from senthire.billing import service as billing
from senthire.config import get_settings
from senthire.db.models import Application, AuditLog, Candidate, CandidateProfileRow, Document
from senthire.db.session import get_sessionmaker
from senthire.domain.derived import compute_derived
from senthire.domain.profile import compose_profile_document
from senthire.extraction.extractor import ExtractionFailed, extract_pdf
from senthire.extraction.pdf import EncryptedPdfError
from senthire.normalize.profile import normalize_profile
from senthire.screening.injection import scan as scan_for_injection
from senthire.services import storage
from senthire.workers.celery_app import celery_app

TRANSIENT = (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError)


@celery_app.task(
    name="senthire.intake.document",
    autoretry_for=TRANSIENT,
    retry_backoff=5,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=5,
)
def intake_document(org_id: str, job_id: str, s3_key: str, filename: str) -> dict:
    settings = get_settings()
    session = get_sessionmaker()()
    try:
        size = storage.object_size(s3_key)
        if size > settings.max_upload_bytes:
            return _register_failed(
                session, org_id, job_id, s3_key, filename, size, "file_too_large"
            )

        data = storage.get_object_bytes(s3_key)
        if not data.startswith(b"%PDF"):
            return _register_failed(
                session, org_id, job_id, s3_key, filename, size, "unsupported_type"
            )

        sha = hashlib.sha256(data).hexdigest()
        existing = session.scalar(
            select(Document).where(Document.org_id == uuid.UUID(org_id), Document.sha256 == sha)
        )
        if existing is not None:
            return _handle_duplicate(session, existing, uuid.UUID(job_id))

        doc = Document(
            org_id=uuid.UUID(org_id),
            upload_job_id=uuid.UUID(job_id),
            original_filename=filename,
            sha256=sha,
            s3_key=s3_key,
            mime="application/pdf",
            size_bytes=size,
        )
        session.add(doc)
        # Billing meter: a new, valid, non-duplicate CV — the point where model
        # cost starts. Same transaction as the document insert, so a lost race
        # (IntegrityError below) also rolls the counter back.
        billing.record_cvs_processed(session, doc.org_id)
        try:
            session.commit()
        except IntegrityError:  # concurrent upload of the same bytes won the race
            session.rollback()
            existing = session.scalar(
                select(Document).where(Document.org_id == uuid.UUID(org_id), Document.sha256 == sha)
            )
            return _handle_duplicate(session, existing, uuid.UUID(job_id))

        return _parse_document(session, doc, uuid.UUID(job_id), data)
    finally:
        session.close()


@celery_app.task(
    name="senthire.intake.link_after_parse",
    bind=True,
    max_retries=12,
    default_retry_delay=10,
)
def link_after_parse(self, document_id: str, job_id: str) -> dict:
    """A duplicate upload arrived while the first copy was still parsing:
    link this job once that parse lands."""
    session = get_sessionmaker()()
    try:
        doc = session.get(Document, uuid.UUID(document_id))
        if doc is None or doc.parse_status == "failed":
            return {"status": "gone_or_failed"}
        if doc.parse_status != "parsed" or doc.candidate_id is None:
            raise self.retry()
        _ensure_application(session, doc, uuid.UUID(job_id))
        session.commit()
        return {"status": "linked"}
    finally:
        session.close()


def _handle_duplicate(session: Session, doc: Document, job_id: uuid.UUID) -> dict:
    if doc.parse_status == "parsed" and doc.candidate_id is not None:
        _ensure_application(session, doc, job_id)
        session.commit()
        return {"status": "duplicate_reused", "document_id": str(doc.id)}
    if doc.parse_status in {"pending", "parsing"}:
        link_after_parse.delay(str(doc.id), str(job_id))
        return {"status": "duplicate_pending", "document_id": str(doc.id)}
    return {"status": "duplicate_unusable", "document_id": str(doc.id)}


def _parse_document(session: Session, doc: Document, job_id: uuid.UUID, data: bytes) -> dict:
    claimed = session.execute(
        update(Document)
        .where(Document.id == doc.id, Document.parse_status == "pending")
        .values(parse_status="parsing")
    )
    session.commit()
    if claimed.rowcount == 0:  # another worker holds it (docs/08 §2)
        return {"status": "already_claimed", "document_id": str(doc.id)}

    try:
        outcome = extract_pdf(data)
    except EncryptedPdfError:
        return _mark_failed(session, doc, "encrypted_pdf")
    except ExtractionFailed as exc:
        return _mark_failed(session, doc, str(exc))
    except TRANSIENT:
        # release the claim so the retried delivery can pick it up again
        session.execute(
            update(Document)
            .where(Document.id == doc.id, Document.parse_status == "parsing")
            .values(parse_status="pending")
        )
        session.commit()
        raise

    profile = outcome.profile
    if profile.document_kind != "cv":
        doc.document_kind = profile.document_kind
        doc.parse_status = "parsed"  # stored & classified, deliberately not screened
        doc.page_count = outcome.page_count
        session.commit()
        return {"status": "not_a_cv", "kind": profile.document_kind, "document_id": str(doc.id)}
    if profile.multi_person:
        return _mark_failed(session, doc, "multi_person_document")

    # Deterministic vocabulary before anything is derived or stored: the
    # extractor's own canonical strings vary per document, and every downstream
    # comparison assumes one vocabulary (docs/05 §2).
    profile, normalization = normalize_profile(profile, raw_text=outcome.raw_text)
    derived = compute_derived(profile)
    profile_doc = compose_profile_document(
        profile,
        derived,
        model=outcome.model,
        prompt_version=outcome.prompt_version,
        path=outcome.path,
        confidence=profile.confidence,
        normalization=normalization.as_dict(),
        # Scanned before any judging model sees the document, so a CV that
        # tries to instruct the evaluator is on the record either way.
        integrity=scan_for_injection(outcome.raw_text),
    )

    candidate = _resolve_candidate(session, doc.org_id, profile)
    row = CandidateProfileRow(
        org_id=doc.org_id,
        document_id=doc.id,
        candidate_id=candidate.id,
        version=1,
        profile=profile_doc,
        raw_text=outcome.raw_text,
        extraction_confidence=profile.confidence,
        extractor_model=outcome.model,
        extractor_prompt_version=outcome.prompt_version,
        pipeline_version=PIPELINE_VERSION,
    )
    session.add(row)

    doc.candidate_id = candidate.id
    doc.document_kind = "cv"
    doc.page_count = outcome.page_count
    doc.parse_status = "parsed"

    _ensure_application(session, doc, job_id)
    session.add(
        AuditLog(
            org_id=doc.org_id,
            actor=None,
            event="document.parsed",
            entity={"type": "document", "id": str(doc.id)},
            detail={
                "path": outcome.path,
                "model": outcome.model,
                "input_tokens": outcome.input_tokens,
                "output_tokens": outcome.output_tokens,
                "confidence": profile.confidence,
            },
        )
    )
    session.commit()
    return {"status": "parsed", "document_id": str(doc.id), "candidate_id": str(candidate.id)}


def _resolve_candidate(session: Session, org_id: uuid.UUID, profile) -> Candidate:
    """Conservative identity resolution (docs/03 §5): strong keys only."""
    emails = [e.strip().lower() for e in profile.identity.emails if e and "@" in e]
    candidate = None
    if emails:
        candidate = session.scalar(
            select(Candidate).where(
                Candidate.org_id == org_id,
                Candidate.primary_email.in_(emails),
                Candidate.erased_at.is_(None),
            )
        )
    if candidate is None:
        candidate = Candidate(
            org_id=org_id,
            primary_email=emails[0] if emails else None,
            primary_phone=(profile.identity.phones or [None])[0],
            display_name=profile.identity.full_name,
            identity_keys=[hashlib.sha256(e.encode()).hexdigest() for e in emails],
        )
        session.add(candidate)
        try:
            session.flush()
        except IntegrityError:
            # Another worker resolved the same person first (migration 0009).
            # Take theirs: two rows for one candidate would show up twice in the
            # ranking and be screened twice.
            session.rollback()
            candidate = session.scalar(
                select(Candidate).where(
                    Candidate.org_id == org_id,
                    Candidate.primary_email.in_(emails),
                    Candidate.erased_at.is_(None),
                )
            )
            if candidate is None:  # not the race after all — surface it
                raise
    elif profile.identity.full_name and not candidate.display_name:
        candidate.display_name = profile.identity.full_name
    return candidate


def _ensure_application(session: Session, doc: Document, job_id: uuid.UUID) -> None:
    exists = session.scalar(
        select(Application).where(
            Application.job_id == job_id, Application.candidate_id == doc.candidate_id
        )
    )
    if exists is None:
        session.add(
            Application(
                org_id=doc.org_id,
                job_id=job_id,
                candidate_id=doc.candidate_id,
                document_id=doc.id,
                status="profiled",
            )
        )
    elif exists.status == "received":
        exists.status = "profiled"


def _mark_failed(session: Session, doc: Document, reason: str) -> dict:
    doc.parse_status = "failed"
    doc.parse_error = {"reason": reason, "at": datetime.now(UTC).isoformat()}
    session.commit()
    return {"status": "failed", "reason": reason, "document_id": str(doc.id)}


def _register_failed(
    session: Session,
    org_id: str,
    job_id: str,
    s3_key: str,
    filename: str,
    size: int,
    reason: str,
) -> dict:
    doc = Document(
        org_id=uuid.UUID(org_id),
        upload_job_id=uuid.UUID(job_id),
        original_filename=filename,
        sha256=f"invalid:{uuid.uuid4()}",  # placeholder; file was rejected pre-hash
        s3_key=s3_key,
        mime="application/octet-stream",
        size_bytes=size,
        parse_status="unsupported" if reason == "unsupported_type" else "failed",
        parse_error={"reason": reason},
    )
    session.add(doc)
    session.commit()
    return {"status": "rejected", "reason": reason, "document_id": str(doc.id)}
