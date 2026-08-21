"""Password hashing, opaque session tokens, and session lifecycle.

Design (docs/01 §2 tenancy): a company signs up once and gets an organization
(workspace); any number of colleagues join the same organization through
invitations. Browser auth is a server-side session — the cookie carries an
opaque random token, and the database stores only its sha256, so neither a
cookie log nor a database dump alone reproduces a usable credential.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from senthire.config import get_settings
from senthire.db.models import AuthSession, User

_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db: Session, user_id: uuid.UUID) -> str:
    """Create a session row and return the raw token (only ever held by the cookie)."""
    settings = get_settings()
    token = new_token()
    db.add(
        AuthSession(
            user_id=user_id,
            token_hash=hash_token(token),
            expires_at=datetime.now(UTC) + timedelta(days=settings.session_ttl_days),
        )
    )
    return token


def resolve_session(db: Session, token: str) -> User | None:
    """Return the active user for a raw cookie token, or None."""
    row = db.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(AuthSession.token_hash == hash_token(token))
    ).first()
    if row is None:
        return None
    auth_session, user = row
    if auth_session.revoked_at is not None:
        return None
    if auth_session.expires_at < datetime.now(UTC):
        return None
    if not user.is_active:
        return None
    return user


def revoke_session(db: Session, token: str) -> None:
    auth_session = db.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_token(token))
    )
    if auth_session is not None and auth_session.revoked_at is None:
        auth_session.revoked_at = datetime.now(UTC)


def revoke_all_sessions(
    db: Session, user_id: uuid.UUID, *, keep_token: str | None = None
) -> None:
    """Revoke a user's sessions, optionally sparing the one making the request.

    The sparing matters for password change: the browser's fetch can be
    cancelled by a navigation *after* the server has already processed the
    request, so a replacement cookie in the response is not guaranteed to
    arrive. If the current session dies with the rest, that race logs the user
    out of the very device they changed the password from. Keeping it is also
    the honest semantics — "log out my *other* devices".
    """
    keep_hash = hash_token(keep_token) if keep_token else None
    for auth_session in db.scalars(
        select(AuthSession).where(
            AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
        )
    ):
        if keep_hash is not None and auth_session.token_hash == keep_hash:
            continue
        auth_session.revoked_at = datetime.now(UTC)


def session_cookie_kwargs() -> dict:
    """Cookie attributes shared by login/signup/accept (set) and logout (delete)."""
    settings = get_settings()
    return {
        "key": settings.session_cookie_name,
        "httponly": True,
        "samesite": "lax",
        "secure": settings.secure_cookies,
        "path": "/",
    }
