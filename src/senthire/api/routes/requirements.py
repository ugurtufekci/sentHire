"""Stage 2 endpoints: compile HR input into a draft spec, review, confirm.

Compilation is an LLM call, so it runs in a worker; the API returns 202 and the
client polls the spec row until status becomes "draft" (docs/01 §3).
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from senthire.api.deps import get_db, get_org, parse_uuid
from senthire.config import get_settings
from senthire.db.models import AuditLog, EvaluationSpecRow, Job, Organization
from senthire.domain.spec import EvaluationSpec

router = APIRouter(tags=["requirements"])


class CompileRequest(BaseModel):
    natural_language_text: str
    locale: str = "tr"


class ConfirmRequest(BaseModel):
    spec: dict | None = None  # HR-edited spec; omit to confirm the draft unchanged


def _get_job(job_id: str, org: Organization, session: Session) -> Job:
    job = session.get(Job, parse_uuid(job_id, "job_id"))
    if job is None or job.org_id != org.id:
        raise HTTPException(status_code=404, detail="job not found")
    return job


def _spec_out(row: EvaluationSpecRow, *, full: bool = False) -> dict:
    out = {
        "spec_id": str(row.id),
        "job_id": str(row.job_id),
        "version": row.version,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
    }
    if full:
        out["source_nl_text"] = row.source_nl_text
        out["spec"] = row.spec
    return out


@router.post("/jobs/{job_id}/requirements/compile", status_code=202)
def compile_requirements(
    job_id: str,
    payload: CompileRequest,
    org: Organization = Depends(get_org),
    session: Session = Depends(get_db),
) -> dict:
    job = _get_job(job_id, org, session)
    if not payload.natural_language_text.strip():
        raise HTTPException(status_code=422, detail="natural_language_text is empty")

    settings = get_settings()
    next_version = (
        session.scalar(
            select(func.max(EvaluationSpecRow.version)).where(EvaluationSpecRow.job_id == job.id)
        )
        or 0
    ) + 1
    row = EvaluationSpecRow(
        org_id=org.id,
        job_id=job.id,
        version=next_version,
        status="compiling",
        spec={},
        source_nl_text=payload.natural_language_text,
        compiler_model=settings.compiler_model,
        compiler_prompt_version=settings.prompt_versions["compile"],
    )
    session.add(row)
    session.flush()

    from senthire.workers.tasks.screen import compile_spec_task

    compile_spec_task.delay(str(row.id))
    return _spec_out(row)


@router.get("/jobs/{job_id}/requirements")
def list_specs(
    job_id: str,
    org: Organization = Depends(get_org),
    session: Session = Depends(get_db),
) -> list[dict]:
    job = _get_job(job_id, org, session)
    rows = session.scalars(
        select(EvaluationSpecRow)
        .where(EvaluationSpecRow.job_id == job.id)
        .order_by(EvaluationSpecRow.version.desc())
    ).all()
    return [_spec_out(r) for r in rows]


@router.get("/requirements/{spec_id}")
def get_spec(
    spec_id: str,
    org: Organization = Depends(get_org),
    session: Session = Depends(get_db),
) -> dict:
    row = session.get(EvaluationSpecRow, parse_uuid(spec_id, "spec_id"))
    if row is None or row.org_id != org.id:
        raise HTTPException(status_code=404, detail="spec not found")
    return _spec_out(row, full=True)


@router.post("/requirements/{spec_id}/confirm")
def confirm_spec(
    spec_id: str,
    payload: ConfirmRequest,
    org: Organization = Depends(get_org),
    session: Session = Depends(get_db),
) -> dict:
    row = session.get(EvaluationSpecRow, parse_uuid(spec_id, "spec_id"))
    if row is None or row.org_id != org.id:
        raise HTTPException(status_code=404, detail="spec not found")
    if row.status != "draft":
        raise HTTPException(status_code=409, detail=f"spec is {row.status}, expected draft")

    if payload.spec is not None:
        candidate = dict(payload.spec)
        candidate["version"] = row.version
        try:
            validated = EvaluationSpec.model_validate(
                {k: v for k, v in candidate.items() if k != "compiler"}
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"invalid spec: {exc}") from exc
        doc = validated.model_dump()
        doc["compiler"] = (row.spec or {}).get("compiler")
        row.spec = doc

    # exactly one confirmed spec per job at a time (docs/04 §3)
    session.execute(
        EvaluationSpecRow.__table__.update()
        .where(
            EvaluationSpecRow.job_id == row.job_id,
            EvaluationSpecRow.status == "confirmed",
        )
        .values(status="superseded")
    )
    row.status = "confirmed"
    row.confirmed_at = datetime.now(UTC)
    session.add(
        AuditLog(
            org_id=org.id,
            actor=None,
            event="spec.confirmed",
            entity={"type": "evaluation_spec", "id": str(row.id)},
            detail={"version": row.version, "edited": payload.spec is not None},
        )
    )
    return _spec_out(row, full=True)
