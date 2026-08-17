from senthire.domain.scoring import RequirementVerdict, score
from senthire.domain.spec import DeterministicCheck, EvaluationSpec, Requirement, SemanticCheck
from senthire.screening.assemble import (
    build_result_document,
    merge_verdicts,
    verdicts_from_result_document,
)


def v(req_id, verdict, stage, s=None, conf=0.9, borderline=False) -> RequirementVerdict:
    return RequirementVerdict(
        req_id=req_id, verdict=verdict, score=s, confidence=conf,
        source_stage=stage, borderline=borderline,
    )


def spec_with(*reqs) -> EvaluationSpec:
    return EvaluationSpec(requirements=list(reqs))


DET_REQ = Requirement(
    req_id="D", category="education", type="hard", evaluator="deterministic",
    deterministic=DeterministicCheck(predicate={"field": "education.highest_degree_rank", "op": ">=", "value": 3}),
)
SEM_REQ = Requirement(
    req_id="S", category="skills", type="scored", evaluator="semantic",
    semantic=SemanticCheck(rubric="…"),
)
HYBRID_HARD = Requirement(
    req_id="HH", category="relevant_experience", type="hard", evaluator="hybrid",
    deterministic=DeterministicCheck(predicate={"field": "derived.total_experience_months", "op": ">=", "value": 36}),
    semantic=SemanticCheck(rubric="count only B2B roles"),
)
HYBRID_SCORED = Requirement(
    req_id="HS", category="skills", type="scored", evaluator="hybrid",
    deterministic=DeterministicCheck(predicate={"field": "tools_technologies", "op": "contains", "value": ["crm"]}),
    semantic=SemanticCheck(rubric="depth of CRM usage"),
)


def test_deterministic_verdict_is_authoritative():
    merged = merge_verdicts(
        spec_with(DET_REQ),
        det={"D": v("D", "met", "deterministic", s=1.0, conf=1.0)},
        light={"D": v("D", "not_met", "light")},  # a model may not override code
    )
    assert merged["D"].verdict == "met"
    assert merged["D"].source_stage == "deterministic"


def test_deep_overrides_light_for_semantic():
    merged = merge_verdicts(
        spec_with(SEM_REQ),
        det={},
        light={"S": v("S", "not_met", "light", s=0.0)},
        deep={"S": v("S", "met", "deep", s=0.9)},
    )
    assert merged["S"].verdict == "met"
    assert merged["S"].source_stage == "deep"


def test_hybrid_hard_takes_most_severe_side():
    # deterministic says months are fine; semantic says none of it is B2B → gate fails
    merged = merge_verdicts(
        spec_with(HYBRID_HARD),
        det={"HH": v("HH", "met", "deterministic", s=1.0, conf=1.0)},
        light={"HH": v("HH", "not_met", "light", s=0.0)},
    )
    assert merged["HH"].verdict == "not_met"

    # and the reverse: arithmetic fails → semantic optimism cannot pass the gate
    merged = merge_verdicts(
        spec_with(HYBRID_HARD),
        det={"HH": v("HH", "not_met", "deterministic", s=0.0, conf=1.0)},
        light={"HH": v("HH", "met", "light", s=1.0)},
    )
    assert merged["HH"].verdict == "not_met"


def test_hybrid_hard_borderline_flag_survives_merge():
    merged = merge_verdicts(
        spec_with(HYBRID_HARD),
        det={"HH": v("HH", "not_met", "deterministic", borderline=True)},
        light={"HH": v("HH", "met", "light", s=0.9)},
    )
    assert merged["HH"].borderline is True


def test_hybrid_scored_prefers_semantic_falls_back_to_det():
    merged = merge_verdicts(
        spec_with(HYBRID_SCORED),
        det={"HS": v("HS", "met", "deterministic", s=1.0, conf=1.0)},
        light={"HS": v("HS", "unknown", "light")},  # model abstained → det signal counts
    )
    assert merged["HS"].verdict == "met"
    assert merged["HS"].source_stage == "deterministic"


def test_result_document_round_trip_and_rejection_reasons():
    spec = spec_with(DET_REQ, SEM_REQ)
    verdicts = {
        "D": v("D", "not_met", "deterministic", s=0.0, conf=1.0),
        "S": v("S", "met", "light", s=0.8),
    }
    sr = score(spec, verdicts)
    doc = build_result_document(spec, verdicts, sr, stage_reached="light")
    assert doc["gate"]["status"] == "fail"
    assert doc["rejection_reasons"][0]["req_id"] == "D"
    restored = verdicts_from_result_document(doc)
    assert restored["S"].verdict == "met"
    assert restored["D"].source_stage == "deterministic"
    # re-scoring restored verdicts reproduces the same result (memoization contract)
    assert score(spec, restored).final_score == sr.final_score
