"""Plan catalog: pricing is by monthly CV volume, not by seats.

The unit metered is a CV accepted by intake (valid PDF, not a duplicate) — the
point where model cost starts. Re-screening already-processed CVs is memoized
and free, so it is not metered.

PLACEHOLDER PRICING: the TRY amounts below are working placeholders — set the
final price points before launch. Plan names are Turkish product copy.
"""

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    monthly_price_try: int  # whole TRY; 0 = free
    cv_quota_per_month: int


TRIAL_PLAN_ID = "deneme"

PLANS: tuple[Plan, ...] = (
    Plan(id=TRIAL_PLAN_ID, name="Deneme", monthly_price_try=0, cv_quota_per_month=25),
    Plan(id="baslangic", name="Başlangıç", monthly_price_try=990, cv_quota_per_month=300),
    Plan(id="profesyonel", name="Profesyonel", monthly_price_try=2990, cv_quota_per_month=1500),
    Plan(id="kurumsal", name="Kurumsal", monthly_price_try=9900, cv_quota_per_month=10000),
)

PLANS_BY_ID: dict[str, Plan] = {p.id: p for p in PLANS}


def get_plan(plan_id: str) -> Plan | None:
    return PLANS_BY_ID.get(plan_id)


def current_period(now: datetime | None = None) -> str:
    """Usage-counter key: the calendar month, e.g. '2026-08' (UTC)."""
    return (now or datetime.now(UTC)).strftime("%Y-%m")
