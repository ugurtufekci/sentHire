"""Fixed-window abuse throttling for the auth surface.

The auth endpoints were the one door with no lock pacing: a password list
could be tried without limit, the forgot-password form could probe or bomb,
and signup could be scripted. Counters live in the database — the same
choice the rest of the system makes (the DB is the state machine), it works
identically across any number of API processes, and it needs no service the
stack does not already run.

Two shapes of counting:

- ``hit`` counts an event and answers "over the limit?" in one atomic
  upsert. It COMMITS the session immediately: a denied or failed request
  still raises an exception, and the count must survive that.
- ``peek`` answers without counting — the login lockout must reject even a
  CORRECT password while the window stands, and checking is not an attempt.

Scopes never contain the raw identifier: e-mail scopes carry a sha256
prefix, so the counter table holds no address to erase. (Unsalted — the
table is short-lived pacing state, not an identity store.)
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from fastapi import HTTPException, Request
from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from senthire.config import get_settings
from senthire.db.models import AuthThrottle

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    count: int
    retry_after_seconds: int


def hashed(value: str) -> str:
    return sha256(value.strip().lower().encode("utf-8")).hexdigest()[:16]


def scope_for(kind: str, value: str) -> str:
    return f"{kind}:{hashed(value)}"


def client_ip(request: Request) -> str:
    if get_settings().trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _retry_after(window_start: datetime, window_seconds: int, now: datetime) -> int:
    remaining = (window_start + timedelta(seconds=window_seconds)) - now
    return max(1, int(remaining.total_seconds()))


def hit(
    session: Session,
    scope: str,
    *,
    limit: int,
    window_seconds: int,
    now: datetime | None = None,
) -> Decision:
    """Count one event in the scope's current window, atomically, and commit."""
    now = now or datetime.now(UTC)
    expired_before = now - timedelta(seconds=window_seconds)
    statement = pg_insert(AuthThrottle).values(scope=scope, window_start=now, count=1)
    statement = statement.on_conflict_do_update(
        index_elements=[AuthThrottle.scope],
        set_={
            "count": case(
                (AuthThrottle.window_start <= expired_before, 1),
                else_=AuthThrottle.count + 1,
            ),
            "window_start": case(
                (AuthThrottle.window_start <= expired_before, now),
                else_=AuthThrottle.window_start,
            ),
        },
    ).returning(AuthThrottle.count, AuthThrottle.window_start)
    count, window_start = session.execute(statement).one()
    session.commit()
    return Decision(
        allowed=count <= limit,
        count=count,
        retry_after_seconds=_retry_after(window_start, window_seconds, now),
    )


def peek(
    session: Session,
    scope: str,
    *,
    limit: int,
    window_seconds: int,
    now: datetime | None = None,
) -> Decision:
    """Answer "is this scope locked?" without counting the question."""
    now = now or datetime.now(UTC)
    row = session.execute(
        select(AuthThrottle.count, AuthThrottle.window_start).where(AuthThrottle.scope == scope)
    ).one_or_none()
    if row is None:
        return Decision(allowed=True, count=0, retry_after_seconds=0)
    count, window_start = row
    if window_start <= now - timedelta(seconds=window_seconds):
        return Decision(allowed=True, count=0, retry_after_seconds=0)
    return Decision(
        allowed=count < limit,
        count=count,
        retry_after_seconds=_retry_after(window_start, window_seconds, now),
    )


def clear(session: Session, scope: str) -> None:
    session.query(AuthThrottle).filter(AuthThrottle.scope == scope).delete()


def refuse(decision: Decision) -> None:
    minutes = max(1, decision.retry_after_seconds // 60)
    raise HTTPException(
        status_code=429,
        detail=f"çok fazla deneme — {minutes} dakika sonra tekrar deneyin",
        headers={"Retry-After": str(decision.retry_after_seconds)},
    )


def enforce_hit(
    session: Session, scope: str, *, limit: int, window_seconds: int
) -> None:
    """Count this event; refuse the request when the window is over budget.
    The first refused event in a window leaves an ops trail."""
    decision = hit(session, scope, limit=limit, window_seconds=window_seconds)
    if not decision.allowed:
        if decision.count == limit + 1:  # first refusal in this window
            logger.warning("auth throttled: scope=%s count=%d", scope, decision.count)
        refuse(decision)
