from senthire.domain.spec import DeterministicCheck, EvaluationSpec, Requirement, SemanticCheck
from senthire.screening.deterministic import run_deterministic_stage, semantic_requirements


def hard_req(req_id="H_exp", months=36, tolerance=0.1, missing_policy="unknown") -> Requirement:
    return Requirement(
        req_id=req_id,
        category="relevant_experience",
        type="hard",
        evaluator="deterministic",
        deterministic=DeterministicCheck(
            predicate={"field": "derived.total_experience_months", "op": ">=", "value": months},
            borderline_tolerance=tolerance,
        ),
        missing_policy=missing_policy,
    )


def spec_of(*reqs) -> EvaluationSpec:
    return EvaluationSpec(requirements=list(reqs))


def profile(months=None) -> dict:
    derived = {} if months is None else {"total_experience_months": months}
    return {"derived": derived, "location": {}, "languages": [], "industries": [],
            "tools_technologies": [], "skills": [], "certifications": [], "experience": []}


def test_clean_fail_knocks_out():
    result = run_deterministic_stage(spec_of(hard_req()), profile(months=12))
    assert result.knocked_out is True
    assert result.knockout_reasons == ["H_exp"]
    assert result.verdicts["H_exp"].verdict == "not_met"


def test_borderline_fail_does_not_knock_out():
    result = run_deterministic_stage(spec_of(hard_req()), profile(months=34))
    assert result.knocked_out is False
    assert result.borderline is True
    assert result.verdicts["H_exp"].borderline is True


def test_unknown_passes_through_by_default():
    result = run_deterministic_stage(spec_of(hard_req()), profile(months=None))
    assert result.knocked_out is False
    assert result.verdicts["H_exp"].verdict == "unknown"
    assert result.verdicts["H_exp"].info_status == "missing"


def test_missing_policy_fail_turns_unknown_into_knockout():
    result = run_deterministic_stage(
        spec_of(hard_req(missing_policy="fail")), profile(months=None)
    )
    assert result.knocked_out is True
    assert result.verdicts["H_exp"].verdict == "not_met"


def test_scored_requirements_never_knock_out():
    scored = Requirement(
        req_id="S_loc",
        category="location",
        type="scored",
        evaluator="deterministic",
        deterministic=DeterministicCheck(
            predicate={"field": "location.city_canonical", "op": "==", "value": "Ankara"}
        ),
    )
    result = run_deterministic_stage(spec_of(scored), {"location": {"city_canonical": "Istanbul"}})
    assert result.knocked_out is False
    assert result.verdicts["S_loc"].verdict == "not_met"


def test_disqualifier_knocks_out_when_met():
    dq = Requirement(
        req_id="D1",
        category="custom",
        type="disqualifier",
        evaluator="deterministic",
        deterministic=DeterministicCheck(
            predicate={"field": "industries", "op": "contains", "value": ["gambling"]}
        ),
    )
    result = run_deterministic_stage(spec_of(dq), {**profile(months=60), "industries": ["gambling"]})
    assert result.knocked_out is True


def test_semantic_requirements_payload():
    sem = Requirement(
        req_id="S1", category="skills", type="scored", evaluator="semantic",
        semantic=SemanticCheck(rubric="judge X"), label={"tr": "X", "en": "X"},
    )
    payload = semantic_requirements(spec_of(hard_req(), sem))
    assert [p["req_id"] for p in payload] == ["S1"]
    assert payload[0]["rubric"] == "judge X"
