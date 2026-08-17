from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from senthire.api.deps import get_db, get_org
from senthire.db.models import JobTemplate

router = APIRouter(tags=["templates"], dependencies=[Depends(get_org)])


@router.get("/templates")
def list_templates(session: Session = Depends(get_db)) -> list[dict]:
    rows = session.scalars(select(JobTemplate).order_by(JobTemplate.title)).all()
    return [
        {
            "id": str(t.id),
            "slug": t.slug,
            "locale": t.locale,
            "title": t.title,
            "requirement_count": len(t.spec_seed.get("requirements", [])),
        }
        for t in rows
    ]


@router.get("/templates/{slug}")
def get_template(slug: str, session: Session = Depends(get_db)) -> dict:
    t = session.scalar(select(JobTemplate).where(JobTemplate.slug == slug))
    if t is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="template not found")
    return {"id": str(t.id), "slug": t.slug, "locale": t.locale, "title": t.title, "spec_seed": t.spec_seed}
