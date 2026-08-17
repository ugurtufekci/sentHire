import pytest

from senthire.domain.scoring import RequirementVerdict, score
from senthire.domain.spec import (
    DeterministicCheck,
    EvaluationSpec,
    Requirement,
    SemanticCheck,
)


def scored_req(req_id: str, category: str) -> Requirement:
    return Requirement(
        req_id=req_id,
        category=category,
        type="scored",
        evaluator="semantic",
        semantic=SemanticCheck(rubric="…"),
    )


def verdict(req_id: str, kind: str = "met", s: float | None = None, conf: float = 1.0):
    return RequirementVerdict(req_id=req_id, verdict=kind, score=s, confidence=conf)


def worked_example_spec() -> EvaluationSpec:
    """The docs/06 §3 candidate-#042 example, one scored requirement per category."""
    reqs = [
        Requirement(
            req_id="H1", category="relevant_experience", type="hard", evaluator="deterministic",
            deterministic=DeterministicCheck(predicate={"field": "derived.total_experience_months", "op": ">=", "value": 36}),
        ),
        scored_req("R_exp", "relevant_experience"),
        scored_req("R_skills", "skills"),
        scored_req("R_industry", "industry"),
        scored_req("R_stability", "career_stability"),
        scored_req("R_edu", "education"),
        scored_req("R_lang", "language"),
        scored_req("R_loc", "location"),
        scored_req("R_custom", "custom"),
        Requirement(
            req_id="B_saas", category="industry", type="bonus", evaluator="semantic",
            semantic=SemanticCheck(rubric="…"), bonus_points=5,
        ),
        Requirement(
            req_id="P_hopping", category="career_stability", type="penalty", evaluator="deterministic",
            deterministic=DeterministicCheck(
                predicate={"field": "derived.job_changes_last_5y", "op": ">", "value": 3},
                penalty_points=8,
            ),
        ),
    ]
    return EvaluationSpec(requirements=reqs)


def worked_example_verdicts() -> dict[str, RequirementVerdict]:
    subscores = {
        "R_exp": 0.92, "R_skills": 0.68, "R_industry": 0.91, "R_stability": 0.55,
        "R_edu": 1.0, "R_lang": 1.0, "R_loc": 0.0, "R_custom": 0.85,
    }
    v = {rid: verdict(rid, "met", s=s) for rid, s in subscores.items()}
    v["H1"] = verdict("H1", "met")
    v["B_saas"] = verdict("B_saas", "met")
    v["P_hopping"] = verdict("P_hopping", "not_met")  # 3 changes ≤ threshold → no penalty
    return v


def test_worked_example_matches_docs():
    result = score(worked_example_spec(), worked_example_verdicts())
    assert result.gate.status == "pass"
    assert result.base_score == pytest.approx(79.25, abs=0.06)
    assert [a.kind for a in result.adjustments] == ["bonus"]
    assert result.final_score == pytest.approx(84.25, abs=0.06)
    assert result.band == "top"
    assert result.needs_review is False


def test_hard_fail_gates_without_touching_score():
    v = worked_example_verdicts()
    v["H1"] = verdict("H1", "not_met")
    result = score(worked_example_spec(), v)
    assert result.gate.status == "fail"
    assert result.gate.failed == ["H1"]
    assert result.base_score > 0  # still computed for explainability


def test_unknown_hard_passes_gate_flagged_unless_policy_fail():
    spec = worked_example_spec()
    v = worked_example_verdicts()
    v["H1"] = verdict("H1", "unknown")
    result = score(spec, v)
    assert result.gate.status == "pass"
    assert result.gate.unverified == ["H1"]
    assert result.needs_review is True

    spec.by_id("H1").missing_policy = "fail"
    result = score(spec, v)
    assert result.gate.status == "fail"


def test_unknown_scored_redistributes_weight():
    spec = EvaluationSpec(
        weights={"a": 0.5, "b": 0.5},
        requirements=[scored_req("RA", "a"), scored_req("RB", "b")],
    )
    verdicts = {"RA": verdict("RA", "met", s=0.8), "RB": verdict("RB", "unknown")}
    result = score(spec, verdicts)
    assert result.base_score == pytest.approx(80.0)  # b excluded, weight renormalized
    assert result.missing_information == ["RB"]


def test_penalty_applies_when_condition_met():
    spec = worked_example_spec()
    v = worked_example_verdicts()
    v["P_hopping"] = verdict("P_hopping", "met")
    result = score(spec, v)
    penalty = next(a for a in result.adjustments if a.kind == "penalty")
    assert penalty.points == 8
    assert result.final_score == pytest.approx(84.25 - 8, abs=0.06)


def test_bonus_cap():
    spec = EvaluationSpec(
        weights={"a": 1.0},
        bonus_cap=6,
        requirements=[
            scored_req("RA", "a"),
            Requirement(req_id="B1", category="a", type="bonus", evaluator="semantic",
                        semantic=SemanticCheck(rubric="…"), bonus_points=5),
            Requirement(req_id="B2", category="a", type="bonus", evaluator="semantic",
                        semantic=SemanticCheck(rubric="…"), bonus_points=5),
        ],
    )
    verdicts = {
        "RA": verdict("RA", "met", s=0.5),
        "B1": verdict("B1", "met"),
        "B2": verdict("B2", "met"),
    }
    result = score(spec, verdicts)
    assert sum(a.points for a in result.adjustments) == 6  # capped


def test_confidence_damps_toward_neutral_not_zero():
    spec = EvaluationSpec(weights={"a": 1.0}, requirements=[scored_req("RA", "a")])
    low = score(spec, {"RA": verdict("RA", "met", s=1.0, conf=0.0)})
    assert low.base_score == pytest.approx(50.0)  # conf 0 → ×0.5, never ×0
    assert low.needs_review is True  # run confidence < 0.6


def test_disqualifier_fails_gate_and_flags_review():
    spec = EvaluationSpec(
        weights={"a": 1.0},
        requirements=[
            scored_req("RA", "a"),
            Requirement(req_id="D1", category="a", type="disqualifier", evaluator="deterministic",
                        deterministic=DeterministicCheck(predicate={"field": "industries", "op": "contains", "value": ["x"]})),
        ],
    )
    result = score(spec, {"RA": verdict("RA", "met", s=0.9), "D1": verdict("D1", "met")})
    assert result.gate.status == "fail"
    assert "D1" in result.gate.failed
