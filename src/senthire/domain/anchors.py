"""Anchored scoring: what a number on a requirement actually means.

A model asked for "0..1 satisfaction" will happily answer 0.72 for one
candidate and 0.68 for the next. Neither figure is wrong, and the difference
between them is nothing — it is sampling noise wearing three digits of
precision. Rank ten candidates that way and the order is partly arbitrary,
which is exactly the thing this product claims not to do.

So the scale is a ladder with defined rungs. The model chooses the rung whose
*definition* matches the evidence; the code then snaps whatever number came
back to that ladder. Three consequences, all wanted:

- two candidates on the same rung score identically on that requirement, so a
  difference in the final score always traces to a difference in a rung;
- re-running a job cannot move a score by a hair, because a hair is not a rung;
- "why 82 and not 79?" has an answer a person can read.
"""

from senthire.domain.spec import Requirement

# Quarters. Five rungs is enough to separate "barely" from "not at all" —
# which missing-≠-negative needs — without asking for judgment nobody can make
# consistently. The definitions do the work; the numbers only carry it.
DEFAULT_LADDER: list[dict] = [
    {"score": 1.0, "label": {"tr": "Tam karşılıyor", "en": "Fully meets"},
     "definition": "Kriterin tamamı CV'de açık kanıtla karşılanıyor."},
    {"score": 0.75, "label": {"tr": "Büyük ölçüde", "en": "Largely meets"},
     "definition": "Kriter esas olarak karşılanıyor; küçük bir yön eksik veya belirsiz."},
    {"score": 0.5, "label": {"tr": "Yarı yarıya", "en": "Half"},
     "definition": "Kriterin yaklaşık yarısı karşılanıyor; önemli bir yön eksik."},
    {"score": 0.25, "label": {"tr": "Zayıf", "en": "Weak"},
     "definition": "İlgili bir iz var ama kriteri karşılamaktan uzak."},
    {"score": 0.0, "label": {"tr": "Karşılamıyor", "en": "Does not meet"},
     "definition": "Kriteri karşılayan hiçbir kanıt yok."},
]


def ladder_for(requirement: Requirement) -> list[dict]:
    """The rungs this requirement is judged on — its own, or the default."""
    if requirement.semantic is not None and requirement.semantic.anchors:
        return [anchor.model_dump() for anchor in requirement.semantic.anchors]
    return DEFAULT_LADDER


def rungs(requirement: Requirement) -> list[float]:
    return [anchor["score"] for anchor in ladder_for(requirement)]


def snap(requirement: Requirement, value: float | None) -> float | None:
    """Round a model's number to the nearest rung.

    Ties go **down**. A candidate exactly between two rungs has not
    demonstrated the higher one, and rounding people up quietly is how a
    screening system starts flattering everybody.
    """
    if value is None:
        return None
    ladder = sorted(rungs(requirement))
    if not ladder:
        return value
    best = ladder[0]
    for rung in ladder:
        if abs(rung - value) < abs(best - value):
            best = rung
    return best


def rung_label(requirement: Requirement, value: float | None, locale: str = "tr") -> str | None:
    if value is None:
        return None
    for anchor in ladder_for(requirement):
        if abs(anchor["score"] - value) < 1e-9:
            labels = anchor.get("label") or {}
            return labels.get(locale) or next(iter(labels.values()), None)
    return None


def discrimination_report(
    spec, verdicts_per_candidate: list[dict], *, min_candidates: int = 3
) -> list[dict]:
    """Which criteria actually separated candidates, and which did no work.

    A requirement that lands every candidate on the same rung is not screening
    anybody: either the pool is genuinely uniform on it, or — far more often —
    the criterion is too vague to judge or too easy to meet. Either way the
    recruiter should hear it, because they are paying weight for it.
    """
    rows = []
    for req in spec.requirements:
        if req.evaluator not in {"semantic", "hybrid"}:
            continue
        scores = [
            verdicts[req.req_id].get("score")
            for verdicts in verdicts_per_candidate
            if req.req_id in verdicts and verdicts[req.req_id].get("score") is not None
        ]
        unknowns = sum(
            1
            for verdicts in verdicts_per_candidate
            if verdicts.get(req.req_id, {}).get("verdict") == "unknown"
        )
        if not scores:
            rows.append(
                {
                    "req_id": req.req_id,
                    "label": req.display_label("tr"),
                    "distinct_levels": 0,
                    "unknown": unknowns,
                    "flag": "all_unknown" if unknowns else None,
                }
            )
            continue
        distinct = sorted({round(s, 3) for s in scores}, reverse=True)
        flag = None
        if len(scores) >= min_candidates and len(distinct) == 1:
            flag = "no_discrimination"
        elif unknowns and unknowns >= len(verdicts_per_candidate) / 2:
            flag = "mostly_unknown"
        rows.append(
            {
                "req_id": req.req_id,
                "label": req.display_label("tr"),
                "distinct_levels": len(distinct),
                "levels": distinct,
                "unknown": unknowns,
                "flag": flag,
            }
        )
    return rows
