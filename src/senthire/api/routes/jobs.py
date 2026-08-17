from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from senthire.api.deps import get_db, get_org, parse_uuid
from senthire.db.models import Job, JobTemplate, Organization

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
    org: Organization = Depends(get_org),
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
    job = Job(org_id=org.id, title=payload.title, template_id=template_id)
    session.add(job)
    session.flush()
    return _job_out(job)


@router.get("/jobs")
def list_jobs(org: Organization = Depends(get_org), session: Session = Depends(get_db)) -> list[dict]:
    rows = session.scalars(
        select(Job).where(Job.org_id == org.id).order_by(Job.created_at.desc())
    ).all()
    return [_job_out(j) for j in rows]


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str, org: Organization = Depends(get_org), session: Session = Depends(get_db)
) -> dict:
    job = session.get(Job, parse_uuid(job_id, "job_id"))
    if job is None or job.org_id != org.id:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_out(job)
