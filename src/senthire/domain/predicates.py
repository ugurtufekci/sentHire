"""Predicate DSL evaluator (docs/04 §4) — Stage 3's deterministic checks.

Predicates are data, never generated code. Fields resolve through a whitelisted
registry over the stored profile document; unknown fields/ops downgrade safely.
Three-valued logic: PASS / FAIL / UNKNOWN (Kleene), because "the CV doesn't say"
must never silently become "fail" (missing_policy decides, in the caller).
"""

import re
from typing import Any, Literal

Tri = Literal["pass", "fail", "unknown"]

_LANG_PATH = re.compile(r"^languages\['([a-z]{2,3})'\]\.cefr_rank$")

REGISTRY_PATHS = frozenset(
    {
        "derived.total_experience_months",
        "derived.job_count",
        "derived.avg_tenure_months",
        "derived.job_changes_last_5y",
        "derived.max_employment_gap_months",
        "derived.employment_gap_count",
        "derived.highest_degree_rank",
        "derived.current_employment_status",
        "derived.seniority_estimate",
        "education.highest_degree_rank",  # alias of derived.highest_degree_rank
        "location.city_canonical",
        "location.country",
        "industries",
        "tools_technologies",
        "skills.canonical",
        "certifications.name_canonical",
        "experience.title_canonical",
    }
)

OPS = frozenset({"==", "!=", ">", ">=", "<", "<=", "in", "not_in", "contains", "exists"})


class PredicateError(ValueError):
    """Raised for malformed predicates / unknown fields or ops.

    The requirement compiler validates against the same registry, so hitting
    this at runtime means a spec bypassed validation — fail loudly, not softly.
    """


def resolve_field(profile: dict, path: str) -> tuple[Any, bool]:
    """→ (value, present). present=False means the profile doesn't state it."""
    m = _LANG_PATH.match(path)
    if m:
        code = m.group(1)
        from senthire.domain.profile import CEFR_RANK

        for lang in profile.get("languages", []):
            if (lang.get("language") or "").lower() == code:
                cefr = lang.get("cefr")
                if cefr in CEFR_RANK:
                    return CEFR_RANK[cefr], True
                return None, False
        return None, False

    if path not in REGISTRY_PATHS:
        raise PredicateError(f"field not in registry: {path}")

    if path == "education.highest_degree_rank":
        path = "derived.highest_degree_rank"

    if path == "derived.employment_gap_count":
        gaps = (profile.get("derived") or {}).get("employment_gaps")
        return (len(gaps), True) if gaps is not None else (None, False)
    if path == "skills.canonical":
        vals = [s.get("canonical") for s in profile.get("skills", []) if s.get("canonical")]
        return (vals, True) if vals else (None, False)
    if path == "certifications.name_canonical":
        vals = [
            c.get("name_canonical")
            for c in profile.get("certifications", [])
            if c.get("name_canonical")
        ]
        return (vals, True) if vals else (None, False)
    if path == "experience.title_canonical":
        vals = [
            e.get("title_canonical") for e in profile.get("experience", []) if e.get("title_canonical")
        ]
        return (vals, True) if vals else (None, False)

    node: Any = profile
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None, False
        node = node[part]
    if node is None or node == [] or node == "":
        return None, False
    return node, True


def _norm(v: Any) -> Any:
    return v.strip().lower() if isinstance(v, str) else v


def _compare(value: Any, op: str, target: Any) -> Tri:
    try:
        if op == "exists":
            return "pass"  # caller only reaches here when the field is present
        if op == "==":
            return "pass" if _norm(value) == _norm(target) else "fail"
        if op == "!=":
            return "pass" if _norm(value) != _norm(target) else "fail"
        if op in {">", ">=", "<", "<="}:
            value_f, target_f = float(value), float(target)
            ok = {
                ">": value_f > target_f,
                ">=": value_f >= target_f,
                "<": value_f < target_f,
                "<=": value_f <= target_f,
            }[op]
            return "pass" if ok else "fail"
        if op == "in":
            return "pass" if _norm(value) in {_norm(t) for t in target} else "fail"
        if op == "not_in":
            return "pass" if _norm(value) not in {_norm(t) for t in target} else "fail"
        if op == "contains":
            hay = value if isinstance(value, list) else [value]
            hay_set = {_norm(h) for h in hay}
            needles = target if isinstance(target, list) else [target]
            return "pass" if any(_norm(n) in hay_set for n in needles) else "fail"
    except (TypeError, ValueError):
        return "unknown"
    raise PredicateError(f"unknown op: {op}")


def evaluate(predicate: dict, profile: dict) -> Tri:
    if "all" in predicate:
        results = [evaluate(p, profile) for p in predicate["all"]]
        if "fail" in results:
            return "fail"
        return "unknown" if "unknown" in results else "pass"
    if "any" in predicate:
        results = [evaluate(p, profile) for p in predicate["any"]]
        if "pass" in results:
            return "pass"
        return "unknown" if "unknown" in results else "fail"
    if "not" in predicate:
        inner = evaluate(predicate["not"], profile)
        return {"pass": "fail", "fail": "pass", "unknown": "unknown"}[inner]  # type: ignore[return-value]

    field, op = predicate.get("field"), predicate.get("op")
    if not field or not op or op not in OPS:
        raise PredicateError(f"malformed predicate: {predicate}")
    value, present = resolve_field(profile, field)
    if not present:
        return "fail" if op == "exists" else "unknown"
    return _compare(value, op, predicate.get("value"))


def evaluate_with_borderline(
    predicate: dict, profile: dict, tolerance: float | None
) -> tuple[Tri, bool]:
    """Strict result + borderline flag (docs/02 Stage 3).

    borderline=True when the strict result is FAIL but the predicate passes with
    numeric thresholds relaxed by `tolerance` — extraction error insurance:
    34 months vs a 36-month bar is a review case, not a silent rejection.
    """
    strict = evaluate(predicate, profile)
    if strict != "fail" or not tolerance:
        return strict, False
    relaxed = evaluate(_relax(predicate, tolerance), profile)
    return strict, relaxed == "pass"


def _relax(predicate: dict, tol: float) -> dict:
    if "all" in predicate:
        return {"all": [_relax(p, tol) for p in predicate["all"]]}
    if "any" in predicate:
        return {"any": [_relax(p, tol) for p in predicate["any"]]}
    if "not" in predicate:
        return predicate  # negations are not relaxed
    op, value = predicate.get("op"), predicate.get("value")
    if op in {">", ">="} and isinstance(value, (int, float)):
        return {**predicate, "value": value * (1 - tol)}
    if op in {"<", "<="} and isinstance(value, (int, float)):
        return {**predicate, "value": value * (1 + tol)}
    return predicate
