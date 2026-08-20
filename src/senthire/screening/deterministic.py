"""Stage 3 — deterministic pre-screening over the stored profile (docs/02 Stage 3).

Pure functions: (spec, profile) → verdicts + knockout decision. $0, no LLM.
"""

from dataclasses import dataclass, field

from senthire.domain import anchors
from senthire.domain.predicates import PredicateError, evaluate_with_borderline, resolve_field
from senthire.domain.scoring import RequirementVerdict
from senthire.domain.spec import EvaluationSpec

_VERDICT = {"pass": "met", "fail": "not_met", "unknown": "unknown"}


@dataclass
class DeterministicResult:
    verdicts: dict[str, RequirementVerdict] = field(default_factory=dict)
    knocked_out: bool = False
    borderline: bool = False
    knockout_reasons: list[str] = field(default_factory=list)  # req_ids


def _rule_evidence(req, profile: dict) -> list[dict]:
    """The field this rule read and the value it found, as evidence."""
    predicate = req.deterministic.predicate if req.deterministic else {}
    field_path = predicate.get("field")
    if not field_path:
        return []
    try:
        value, present = resolve_field(profile, str(field_path))
    except PredicateError:
        return []
    return [
        {
            "field": field_path,
            "observed": value if present else None,
            "expected": {"op": predicate.get("op"), "value": predicate.get("value")},
            "present": present,
        }
    ]


def run_deterministic_stage(spec: EvaluationSpec, profile: dict) -> DeterministicResult:
    result = DeterministicResult()
    for req in spec.requirements:
        if req.deterministic is None:
            continue
        tri, borderline = evaluate_with_borderline(
            req.deterministic.predicate, profile, req.deterministic.borderline_tolerance
        )
        verdict = _VERDICT[tri]

        # missing_policy=fail turns unknown into a failure for THIS requirement only
        if verdict == "unknown" and req.missing_policy == "fail":
            verdict = "not_met"

        result.verdicts[req.req_id] = RequirementVerdict(
            req_id=req.req_id,
            verdict=verdict,  # type: ignore[arg-type]
            score={"met": 1.0, "not_met": 0.0}.get(verdict),
            confidence=1.0,  # deterministic checks are certain about what they checked
            info_status="explicit" if verdict != "unknown" else "missing",
            # What the rule actually read. A model-judged verdict quotes the CV;
            # a computed one has no quote to give, and without this it arrives on
            # screen as a bare verdict — "Karşılamıyor" with nothing behind it,
            # which is exactly the unexplained answer this product refuses to
            # give (docs/06 §4).
            evidence=_rule_evidence(req, profile),
            source_stage="deterministic",
            borderline=borderline,
        )

        # Only hard deterministic requirements can knock out (docs/02 Stage 3);
        # borderline failures proceed for review instead of silent rejection.
        if req.type == "hard" and req.evaluator in {"deterministic", "hybrid"}:
            if verdict == "not_met":
                if borderline:
                    result.borderline = True
                else:
                    result.knocked_out = True
                    result.knockout_reasons.append(req.req_id)
        elif req.type == "disqualifier" and verdict == "met":
            result.knocked_out = True
            result.knockout_reasons.append(req.req_id)
    return result


def semantic_requirements(spec: EvaluationSpec) -> list[dict]:
    """The requirement payload Stages 4/5 judge: semantic + hybrid only, rubric-first."""
    out = []
    for req in spec.requirements:
        if req.semantic is None:
            continue
        out.append(
            {
                "req_id": req.req_id,
                "category": req.category,
                "label": req.label,
                "type": req.type,
                "importance": req.importance,
                "rubric": req.semantic.rubric,
                # The scale, sent explicitly: a rubric without rungs invites a
                # freehand number, and freehand numbers are not comparable.
                "scale": [
                    {"score": a["score"], "means": a["definition"]}
                    for a in anchors.ladder_for(req)
                ],
            }
        )
    return out
