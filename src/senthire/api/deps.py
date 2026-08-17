import uuid

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from senthire.config import get_settings
from senthire.db.models import Organization
from senthire.db.session import db_session

DEV_ORG_NAME = "Dev Org"


def get_db(session: Session = Depends(db_session)) -> Session:
    return session


def get_org(
    session: Session = Depends(get_db),
    x_api_key: str | None = Header(default=None),
) -> Organization:
    """DEV-ONLY auth placeholder: a static API key maps to an auto-created dev org.

    Replaced wholesale by the signup/session auth milestone (docs/01 §2). Every
    route already receives the org through this single dependency, so swapping
    the mechanism later touches nothing else.
    """
    settings = get_settings()
    if x_api_key != settings.dev_api_key:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")
    org = session.scalar(select(Organization).where(Organization.name == DEV_ORG_NAME))
    if org is None:
        org = Organization(name=DEV_ORG_NAME)
        session.add(org)
        session.flush()
    return org


def parse_uuid(value: str, name: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid {name}") from exc
