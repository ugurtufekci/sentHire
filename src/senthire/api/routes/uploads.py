"""CV intake: presigned direct-to-S3 uploads, then worker-side hashing/dedup/parse
(docs/01 §3, docs/02 Stage 0)."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from senthire.api.deps import get_db, get_org, parse_uuid
from senthire.api.enqueue import enqueue_after_commit
from senthire.billing import service as billing
from senthire.config import get_settings
from senthire.db.models import Job, Organization
from senthire.services import storage

router = APIRouter(tags=["uploads"])

MAX_FILES_PER_BATCH = 500


class UploadRequestFile(BaseModel):
    filename: str
    content_type: str = "application/pdf"


class UploadRequest(BaseModel):
    files: list[UploadRequestFile] = Field(min_length=1, max_length=MAX_FILES_PER_BATCH)


class CompleteFile(BaseModel):
    s3_key: str
    filename: str


class CompleteRequest(BaseModel):
    files: list[CompleteFile] = Field(min_length=1, max_length=MAX_FILES_PER_BATCH)


def _get_job(job_id: str, org: Organization, session: Session) -> Job:
    job = session.get(Job, parse_uuid(job_id, "job_id"))
    if job is None or job.org_id != org.id:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.post("/jobs/{job_id}/uploads")
def request_upload_urls(
    job_id: str,
    payload: UploadRequest,
    org: Organization = Depends(get_org),
    session: Session = Depends(get_db),
) -> dict:
    job = _get_job(job_id, org, session)
    # Quota gate for CV-volume pricing. Metering itself happens at intake (new,
    # valid documents only — duplicates and rejected files are free), so this
    # check is intentionally the strict end of the pair.
    billing.check_cv_quota(session, org.id, len(payload.files))
    out = []
    for f in payload.files:
        key = storage.upload_key(org.id, job.id, f.filename)
        out.append(
            {
                "filename": f.filename,
                "s3_key": key,
                "url": storage.presign_put(key, f.content_type),
                "headers": {"Content-Type": f.content_type},
            }
        )
    return {
        "uploads": out,
        "expires_in_seconds": get_settings().presign_expiry_seconds,
        "max_file_bytes": get_settings().max_upload_bytes,
    }


@router.post("/jobs/{job_id}/uploads/complete", status_code=202)
def complete_uploads(
    job_id: str,
    payload: CompleteRequest,
    org: Organization = Depends(get_org),
    session: Session = Depends(get_db),
) -> dict:
    job = _get_job(job_id, org, session)
    prefix = f"org/{org.id}/"
    from senthire.workers.tasks.parse import intake_document

    enqueued = 0
    for f in payload.files:
        if not f.s3_key.startswith(prefix):  # tenant isolation on keys (docs/01 §2)
            raise HTTPException(status_code=403, detail=f"key outside org scope: {f.s3_key}")
        enqueue_after_commit(
            session, intake_document, str(org.id), str(job.id), f.s3_key, f.filename
        )
        enqueued += 1
    return {"enqueued": enqueued}
