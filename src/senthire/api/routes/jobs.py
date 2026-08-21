from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from senthire.api.deps import get_current_user, get_db, get_org, parse_uuid
from senthire.db.models import Job, JobTemplate, Organization, User

router = APIRouter(tags=["jobs"])


class JobCreate(BaseModel):
    title: str
    template_slug: str | None = None


def _job_out(job: Job) -> dict:
    return {
        "id": str(job.id),
        "title": job.title,
        "status": job.status,
        "template_id": str(job.template_id) if job.template_id else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


@router.post("/jobs", status_code=201)
def create_job(
    payload: JobCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> dict:
    template_id = None
    if payload.template_slug:
        template = session.scalar(
            select(JobTemplate).where(JobTemplate.slug == payload.template_slug)
        )
        if template is None:
            raise HTTPException(status_code=404, detail="template not found")
        template_id = template.id
    job = Job(
        org_id=user.org_id, title=payload.title, template_id=template_id, created_by=user.id
    )
    session.add(job)
    session.flush()
    return _job_out(job)


@router.get("/jobs")
def list_jobs(org: Organization = Depends(get_org), session: Session = Depends(get_db)) -> list[dict]:
    rows = session.scalars(
        select(Job).where(Job.org_id == org.id).order_by(Job.created_at.desc())
    ).all()
    return [_job_out(j) for j in rows]


class JobPatch(BaseModel):
    status: Literal["active", "closed"] | None = None
    title: str | None = None


@router.patch("/jobs/{job_id}")
def update_job(
    job_id: str,
    payload: JobPatch,
    org: Organization = Depends(get_org),
    session: Session = Depends(get_db),
) -> dict:
    """Close a filled or cancelled job so the list stays a worklist.

    Closing changes nothing else on purpose: results, pipeline and exports
    remain readable — a hiring record is a record — and reopening is one call.
    """
    job = session.get(Job, parse_uuid(job_id, "job_id"))
    if job is None or job.org_id != org.id:
        raise HTTPException(status_code=404, detail="job not found")
    if payload.title is not None:
        title = payload.title.strip()
        if not 2 <= len(title) <= 200:
            raise HTTPException(status_code=422, detail="başlık 2–200 karakter olmalı")
        job.title = title
    if payload.status is not None:
        job.status = payload.status
    session.commit()
    return _job_out(job)


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str, org: Organization = Depends(get_org), session: Session = Depends(get_db)
) -> dict:
    job = session.get(Job, parse_uuid(job_id, "job_id"))
    if job is None or job.org_id != org.id:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_out(job)
