import uuid

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from senthire.config import get_settings
from senthire.db.models import Organization, User
from senthire.db.session import db_session
from senthire.services import auth as auth_service

DEV_ORG_NAME = "Dev Org"
DEV_USER_EMAIL = "dev@senthire.local"


def get_db(session: Session = Depends(db_session)) -> Session:
    return session


def _dev_user(session: Session) -> User:
    """Auto-provision the dev org + dev admin for the opt-in X-API-Key backdoor."""
    org = session.scalar(select(Organization).where(Organization.name == DEV_ORG_NAME))
    if org is None:
        org = Organization(name=DEV_ORG_NAME)
        session.add(org)
        session.flush()
    user = session.scalar(select(User).where(User.email == DEV_USER_EMAIL))
    if user is None:
        user = User(org_id=org.id, email=DEV_USER_EMAIL, name="Dev", role="admin")
        session.add(user)
        session.flush()
    return user


def get_current_user(
    request: Request,
    session: Session = Depends(get_db),
    x_api_key: str | None = Header(default=None),
) -> User:
    """Resolve the acting user: session cookie first, then the opt-in dev key.

    Every route receives tenancy through this dependency chain (get_org below),
    so org isolation has exactly one enforcement point.
    """
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        user = auth_service.resolve_session(session, token)
        if user is not None:
            return user
    if settings.dev_api_key and x_api_key == settings.dev_api_key:
        return _dev_user(session)
    raise HTTPException(status_code=401, detail="not authenticated")


def get_org(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> Organization:
    org = session.get(Organization, user.org_id)
    if org is None:  # cannot happen with FK integrity; belt and braces
        raise HTTPException(status_code=401, detail="organization not found")
    return org


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return user


def parse_uuid(value: str, name: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid {name}") from exc
