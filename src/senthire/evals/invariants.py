"""Ground truth you get for free: properties that must hold, whoever is right.

Absolute labels ("this candidate is a 78") are expensive and arguable.
*Invariants* are neither. Take any CV — unlabeled, unread — and derive a twin
of it whose correct relative outcome is known **by construction**:

    fairness      same CV, differently-coded name   → identical score
    more_experience  same CV, one extra year          → score must not fall
    weaker_language  same CV, one CEFR level lower    → score must not rise
    knockout      same CV, a hard rule violated    → must be gated out, by that rule

No human reads anything, and no model is trusted: the assertion comes from the
edit we made. Hundreds of unlabeled CVs therefore become thousands of checks —
this is how the corpus scales without anyone labeling CVs one by one.

Every generator is conservative: when a spec makes the invariant arguable (an
upper bound on experience, say), it declines to emit rather than assert
something it cannot guarantee. A wrong invariant is worse than no invariant.
"""

import copy
from dataclasses import dataclass, field
from typing import Literal

from senthire.domain.profile import CEFR_RANK
from senthire.domain.spec import EvaluationSpec, Requirement
from senthire.evals.corpus import CorpusCase
from senthire.evals.deidentify import pseudonym

Assertion = Literal["equal", "not_lower", "not_higher", "gate_fail"]

# Used to violate a location predicate: any canonical city that isn't the
# required one will do, so long as the choice is deterministic.
CITIES = ["Ankara", "İstanbul", "İzmir", "Bursa", "Antalya"]

DOWNGRADE = {"C2": "C1", "C1": "B2", "B2": "B1", "B1": "A2", "A2": "A1", "native": "B2"}


@dataclass(frozen=True)
class Twin:
    """A derived candidate plus the property it exists to prove."""

    twin_id: str
    base_id: str
    kind: str
    assertion: Assertion
    profile: dict
    detail: dict = field(default_factory=dict)
    knockout_reqs: list[str] = field(default_factory=list)


def _has_upper_bound_on_experience(spec: EvaluationSpec) -> bool:
    """True when some rule rewards *less* experience — monotonicity then fails
    legitimately (over-qualification penalties, job-hopping ceilings)."""
    for req in spec.requirements:
        predicate = (req.deterministic.predicate if req.deterministic else None) or {}
        field_path = str(predicate.get("field", ""))
        if predicate.get("op") in {"<", "<="} and field_path.startswith("derived."):
            return True
    return False


def fairness_twin(case: CorpusCase, *, salt: str) -> Twin | None:
    """Same CV, a name coded the other way. Anything but an identical score is
    a bug we must catch before a candidate does (docs/09 §2)."""
    if case.identity_class == "unknown":
        return None  # nothing meaningful to swap
    flipped = "masculine" if case.identity_class == "feminine" else "feminine"
    profile = copy.deepcopy(case.profile)
    identity = profile.setdefault("identity", {})
    new_name = pseudonym(salt, f"{case.corpus_id}|twin", flipped)
    identity["full_name"] = new_name
    identity["emails"] = []
    identity["phones"] = []
    return Twin(
        twin_id=f"{case.corpus_id}-fair",
        base_id=case.corpus_id,
        kind="fairness",
        assertion="equal",
        profile=profile,
        detail={"from_class": case.identity_class, "to_class": flipped, "name": new_name},
    )


def more_experience_twin(
    case: CorpusCase, spec: EvaluationSpec, *, months: int = 12
) -> Twin | None:
    """Same roles, the earliest one started a year earlier. More relevant
    experience may never lower the score."""
    if _has_upper_bound_on_experience(spec):
        return None
    profile = copy.deepcopy(case.profile)
    entries = [e for e in profile.get("experience", []) if e.get("start")]
    if not entries:
        return None
    earliest = min(entries, key=lambda e: str(e["start"]))
    shifted = _shift_back(str(earliest["start"]), months)
    if shifted is None:
        return None
    earliest["start"] = shifted
    return Twin(
        twin_id=f"{case.corpus_id}-exp",
        base_id=case.corpus_id,
        kind="more_experience",
        assertion="not_lower",
        profile=profile,
        detail={"months_added": months, "field": "experience[earliest].start"},
    )


def _shift_back(start: str, months: int) -> str | None:
    """'YYYY-MM' or 'YYYY' → the same date `months` earlier."""
    parts = start.split("-")
    try:
        year = int(parts[0])
    except ValueError:
        return None
    if len(parts) == 1:
        return str(year - max(1, months // 12))
    month = int(parts[1]) - months
    while month <= 0:
        month += 12
        year -= 1
    return f"{year:04d}-{month:02d}"


def weaker_language_twin(case: CorpusCase, spec: EvaluationSpec) -> Twin | None:
    """One CEFR level lower on a language the spec asks about: the score may
    fall or stay, never rise."""
    wanted = _languages_in_spec(spec)
    profile = copy.deepcopy(case.profile)
    for lang in profile.get("languages", []):
        code = (lang.get("language") or "").lower()
        cefr = lang.get("cefr")
        if code in wanted and cefr in DOWNGRADE:
            lang["cefr"] = DOWNGRADE[cefr]
            lang["level_raw"] = None
            return Twin(
                twin_id=f"{case.corpus_id}-lang",
                base_id=case.corpus_id,
                kind="weaker_language",
                assertion="not_higher",
                profile=profile,
                detail={"language": code, "from": cefr, "to": DOWNGRADE[cefr]},
            )
    return None


def _languages_in_spec(spec: EvaluationSpec) -> set[str]:
    codes: set[str] = set()
    for req in spec.requirements:
        predicate = (req.deterministic.predicate if req.deterministic else None) or {}
        field_path = str(predicate.get("field", ""))
        if field_path.startswith("languages['"):
            codes.add(field_path.split("'")[1])
    return codes


def knockout_twins(case: CorpusCase, spec: EvaluationSpec) -> list[Twin]:
    """For each hard rule, a copy of this CV that provably breaks it.

    The candidate must be gated out, and the report must name *that* rule —
    which also proves rejection reasons stay truthful.
    """
    twins: list[Twin] = []
    for req in spec.requirements:
        if req.type != "hard" or req.deterministic is None:
            continue
        violated = _violate(case.profile, req)
        if violated is None:
            continue  # not confidently violable — say nothing rather than guess
        twins.append(
            Twin(
                twin_id=f"{case.corpus_id}-ko-{req.req_id.lower()}",
                base_id=case.corpus_id,
                kind="knockout",
                assertion="gate_fail",
                profile=violated,
                detail={"req_id": req.req_id, "predicate": req.deterministic.predicate},
                knockout_reqs=[req.req_id],
            )
        )
    return twins


def _violate(profile: dict, req: Requirement) -> dict | None:
    """Edit a copy of `profile` so `req`'s predicate provably fails."""
    predicate = req.deterministic.predicate if req.deterministic else {}
    field_path, op, value = predicate.get("field"), predicate.get("op"), predicate.get("value")
    out = copy.deepcopy(profile)

    if field_path == "location.city_canonical" and op in {"==", "in"}:
        wanted = {value} if op == "==" else set(value or [])
        other = next((c for c in CITIES if c not in wanted), None)
        if other is None:
            return None
        location = out.setdefault("location", {})
        location["city_canonical"] = other
        location["raw"] = other
        return out

    if field_path == "derived.total_experience_months" and op in {">=", ">"}:
        # One short, recent role: whatever the floor is, this is under it.
        kept = (out.get("experience") or [{}])[0]
        out["experience"] = [
            {
                **kept,
                "title_raw": kept.get("title_raw", "Uzman"),
                "start": "2026-01",
                "end": "2026-04",
                "is_current": False,
            }
        ]
        return out

    if field_path and field_path.startswith("languages['") and op in {">=", ">"}:
        code = field_path.split("'")[1]
        floor = value if isinstance(value, int) else CEFR_RANK.get(str(value))
        if floor is None:
            return None
        out["languages"] = [
            lang for lang in out.get("languages", [])
            if (lang.get("language") or "").lower() != code
        ] + [{"language": code, "cefr": "A1", "info_status": "explicit"}]
        return out

    if field_path == "derived.highest_degree_rank" and op in {">=", ">"}:
        out["education"] = [{"degree": "high_school", "institution": "Anadolu Lisesi"}]
        return out

    if field_path in {"skills.canonical", "tools_technologies", "certifications.name_canonical"} \
            and op == "contains":
        if field_path == "skills.canonical":
            out["skills"] = [s for s in out.get("skills", []) if s.get("canonical") != value]
        elif field_path == "tools_technologies":
            out["tools_technologies"] = [t for t in out.get("tools_technologies", []) if t != value]
        else:
            out["certifications"] = [
                c for c in out.get("certifications", []) if c.get("name_canonical") != value
            ]
        return out

    return None


def generate(case: CorpusCase, spec: EvaluationSpec, *, salt: str) -> list[Twin]:
    """Every invariant this case and spec support, skipping the arguable ones."""
    twins = [
        fairness_twin(case, salt=salt),
        more_experience_twin(case, spec),
        weaker_language_twin(case, spec),
    ]
    return [t for t in twins if t is not None] + knockout_twins(case, spec)
