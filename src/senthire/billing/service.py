"""Subscription state, usage metering, and quota enforcement."""

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from senthire.billing.plans import PLANS_BY_ID, TRIAL_PLAN_ID, Plan, current_period
from senthire.db.models import Subscription, UsageCounter


def get_subscription(session: Session, org_id: uuid.UUID) -> Subscription | None:
    return session.scalar(select(Subscription).where(Subscription.org_id == org_id))


def active_plan(session: Session, org_id: uuid.UUID) -> Plan:
    """The plan whose quota applies right now. Anything not 'active' means trial."""
    sub = get_subscription(session, org_id)
    if sub is not None and sub.status == "active":
        plan = PLANS_BY_ID.get(sub.plan_id)
        if plan is not None:
            return plan
    return PLANS_BY_ID[TRIAL_PLAN_ID]


def get_usage(session: Session, org_id: uuid.UUID, period: str | None = None) -> int:
    row = session.scalar(
        select(UsageCounter).where(
            UsageCounter.org_id == org_id,
            UsageCounter.period == (period or current_period()),
        )
    )
    return row.cvs_processed if row else 0


def record_cvs_processed(session: Session, org_id: uuid.UUID, n: int = 1) -> None:
    """Atomic monthly-counter increment (safe under concurrent intake workers)."""
    statement = pg_insert(UsageCounter).values(
        id=uuid.uuid4(), org_id=org_id, period=current_period(), cvs_processed=n
    )
    statement = statement.on_conflict_do_update(
        index_elements=[UsageCounter.org_id, UsageCounter.period],
        set_={"cvs_processed": UsageCounter.cvs_processed + n},
    )
    session.execute(statement)


def assert_within_quota(used: int, plan: Plan, n_new: int) -> None:
    """Pure gate: raises 402 when accepting n_new CVs would exceed the plan quota."""
    remaining = max(0, plan.cv_quota_per_month - used)
    if n_new > remaining:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "cv_quota_exceeded",
                "plan_id": plan.id,
                "quota": plan.cv_quota_per_month,
                "used": used,
                "remaining": remaining,
                "requested": n_new,
            },
        )


def check_cv_quota(session: Session, org_id: uuid.UUID, n_new: int) -> None:
    assert_within_quota(get_usage(session, org_id), active_plan(session, org_id), n_new)


def upsert_pending_checkout(
    session: Session, org_id: uuid.UUID, plan_id: str, provider: str, token: str
) -> Subscription:
    """Start (or restart) a checkout; the callback flips it to active by token."""
    sub = get_subscription(session, org_id)
    now = datetime.now(UTC)
    if sub is None:
        sub = Subscription(
            org_id=org_id, plan_id=plan_id, provider=provider, provider_ref=token
        )
        session.add(sub)
    else:
        sub.plan_id = plan_id
        sub.provider = provider
        sub.provider_ref = token
        sub.status = "pending_checkout"
        sub.canceled_at = None
    sub.updated_at = now
    session.flush()
    return sub


def activate(
    session: Session,
    org_id: uuid.UUID,
    plan_id: str,
    provider: str,
    provider_ref: str | None,
) -> Subscription:
    sub = get_subscription(session, org_id)
    now = datetime.now(UTC)
    if sub is None:
        sub = Subscription(org_id=org_id, plan_id=plan_id, provider=provider)
        session.add(sub)
    sub.plan_id = plan_id
    sub.provider = provider
    sub.provider_ref = provider_ref
    sub.status = "active"
    sub.canceled_at = None
    sub.updated_at = now
    session.flush()
    return sub
