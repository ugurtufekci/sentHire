"""Derived-field computation — plain date math, never delegated to an LLM.

Conventions (unit-tested; see tests/test_derived.py):
- Month granularity. A role's duration is inclusive: 2019-03..2019-03 = 1 month.
- Total experience merges overlapping/adjacent employment intervals first, so
  parallel jobs never double-count.
- A role with no end date counts to "today" only when is_current; otherwise its
  end is unknown → excluded from interval math (still counts in job_count) and
  a warning-friendly None is tolerated everywhere.
- A gap is >= GAP_MIN_MONTHS whole months strictly between merged intervals.
"""

import itertools
import re
from datetime import date

from senthire.domain.profile import (
    DEGREE_RANK,
    DerivedFields,
    EmploymentGap,
    ExtractedProfile,
)
from senthire.normalize import titles

GAP_MIN_MONTHS = 3

_YM = re.compile(r"^(\d{4})(?:[-/.](\d{1,2}))?")
_MY = re.compile(r"^(\d{1,2})[-/.](\d{4})$")

# Seniority words live in the title taxonomy (senthire/normalize), not in a
# tuple here: one vocabulary, maintained as data, matched with Turkish-aware
# folding ("KIDEMLİ".lower() does not equal "kıdemli").
_TITLE_SENIORITY_RANK = {
    "intern": 0, "junior": 1, "mid": 2, "senior": 3,
    "lead": 4, "manager": 4, "director": 4, "executive_suite": 4,
}


def parse_ym(value: str | None) -> date | None:
    """'2019-03' / '2019' / '03/2019' / '2019-03-15' → date(y, m, 1)."""
    if not value:
        return None
    value = value.strip().lower()
    if value in {"present", "current", "halen", "devam", "now", "günümüz"}:
        return None
    m = _MY.match(value)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
        return date(year, month, 1) if 1 <= month <= 12 else date(year, 1, 1)
    m = _YM.match(value)
    if not m:
        return None
    year = int(m.group(1))
    month = int(m.group(2)) if m.group(2) else 1
    if not 1 <= month <= 12:
        month = 1
    if not 1900 <= year <= 2100:
        return None
    return date(year, month, 1)


def months_inclusive(start: date, end: date) -> int:
    return max(0, (end.year - start.year) * 12 + (end.month - start.month) + 1)


def months_between_exclusive(prev_end: date, next_start: date) -> int:
    """Whole months strictly between two months (2019-01 → 2019-03 = 1)."""
    return max(0, (next_start.year - prev_end.year) * 12 + (next_start.month - prev_end.month) - 1)


def _intervals(profile: ExtractedProfile, today: date) -> list[tuple[date, date]]:
    out: list[tuple[date, date]] = []
    for exp in profile.experience:
        start = parse_ym(exp.start)
        if start is None:
            continue
        end = parse_ym(exp.end)
        if end is None:
            if exp.is_current:
                end = date(today.year, today.month, 1)
            else:
                continue  # unknown end → excluded from interval math
        if end < start:
            continue
        out.append((start, end))
    return sorted(out)


def merge_intervals(intervals: list[tuple[date, date]]) -> list[tuple[date, date]]:
    merged: list[tuple[date, date]] = []
    for start, end in intervals:
        if merged and months_between_exclusive(merged[-1][1], start) == 0 and start <= _next_month(
            merged[-1][1]
        ):
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _next_month(d: date) -> date:
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


def compute_derived(profile: ExtractedProfile, today: date | None = None) -> DerivedFields:
    today = today or date.today()
    intervals = _intervals(profile, today)
    merged = merge_intervals(intervals)

    total_months = sum(months_inclusive(s, e) for s, e in merged)

    gaps: list[EmploymentGap] = []
    for (_, prev_end), (next_start, _) in itertools.pairwise(merged):
        gap = months_between_exclusive(prev_end, next_start)
        if gap >= GAP_MIN_MONTHS:
            gaps.append(
                EmploymentGap(
                    from_month=_next_month(prev_end).strftime("%Y-%m"),
                    to_month=next_start.strftime("%Y-%m"),
                    months=gap,
                )
            )

    tenures = [months_inclusive(s, e) for s, e in intervals]

    # employer changes: consecutive roles (by start date) at different employers,
    # counted when the new role started within the last 5 years.
    roles = sorted(
        (e for e in profile.experience if parse_ym(e.start)),
        key=lambda e: parse_ym(e.start),  # type: ignore[arg-type, return-value]
    )
    window_start = date(today.year - 5, today.month, 1)
    changes = 0
    for prev, nxt in itertools.pairwise(roles):
        prev_co = (prev.company or "").strip().lower()
        next_co = (nxt.company or "").strip().lower()
        started = parse_ym(nxt.start)
        if prev_co and next_co and prev_co != next_co and started and started >= window_start:
            changes += 1

    if any(e.is_current for e in profile.experience):
        status = "employed"
    elif profile.experience:
        status = "unemployed"
    else:
        status = "unknown"

    return DerivedFields(
        total_experience_months=total_months,
        job_count=len(profile.experience),
        avg_tenure_months=round(sum(tenures) / len(tenures), 1) if tenures else None,
        job_changes_last_5y=changes,
        employment_gaps=gaps,
        max_employment_gap_months=max((g.months for g in gaps), default=0),
        current_employment_status=status,  # type: ignore[arg-type]
        highest_degree_rank=max(
            (DEGREE_RANK.get(e.degree or "", 0) for e in profile.education), default=0
        ),
        seniority_estimate=_seniority(profile, total_months),  # type: ignore[arg-type]
    )


def _seniority(profile: ExtractedProfile, total_months: int) -> str:
    """Highest seniority any title claims, with tenure as the fallback.

    Titles beat tenure because a title is a statement the employer made; tenure
    is only evidence when nobody said anything. A junior *title* still needs
    short tenure to read as junior — "Uzman Yardımcısı" for eight years is not
    a beginner.
    """
    levels = [titles.classify(e.title_raw).seniority for e in profile.experience if e.title_raw]
    ranks = [_TITLE_SENIORITY_RANK[level] for level in levels if level in _TITLE_SENIORITY_RANK]
    top = max(ranks, default=None)

    if top is not None and top >= 4:
        return "lead"
    if top == 3:
        return "senior"
    if total_months == 0 and not profile.experience:
        return "unknown"
    if top is not None and top <= 1 and total_months < 36:
        return "junior"
    if total_months < 24:
        return "junior"
    if total_months < 72:
        return "mid"
    return "senior"
