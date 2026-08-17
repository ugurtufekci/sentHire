import pytest

from senthire.domain.predicates import (
    PredicateError,
    evaluate,
    evaluate_with_borderline,
    resolve_field,
)

PROFILE = {
    "location": {"city_canonical": "Ankara", "country": "TR"},
    "languages": [{"language": "en", "cefr": "C1"}, {"language": "de", "cefr": None}],
    "industries": ["software_saas", "telecom"],
    "tools_technologies": ["Salesforce", "excel"],
    "skills": [{"name_raw": "CRM", "canonical": "crm"}],
    "certifications": [],
    "experience": [{"title_canonical": "account_executive"}],
    "derived": {
        "total_experience_months": 74,
        "job_changes_last_5y": 2,
        "highest_degree_rank": 3,
        "employment_gaps": [{"months": 5}],
    },
}


def test_numeric_ops():
    assert evaluate({"field": "derived.total_experience_months", "op": ">=", "value": 36}, PROFILE) == "pass"
    assert evaluate({"field": "derived.total_experience_months", "op": "<", "value": 36}, PROFILE) == "fail"


def test_equality_is_case_insensitive():
    assert evaluate({"field": "location.city_canonical", "op": "==", "value": "ankara"}, PROFILE) == "pass"


def test_in_and_contains():
    assert evaluate({"field": "location.city_canonical", "op": "in", "value": ["Ankara", "Istanbul"]}, PROFILE) == "pass"
    assert evaluate({"field": "tools_technologies", "op": "contains", "value": ["salesforce"]}, PROFILE) == "pass"
    assert evaluate({"field": "tools_technologies", "op": "contains", "value": "hubspot"}, PROFILE) == "fail"


def test_language_virtual_path():
    assert evaluate({"field": "languages['en'].cefr_rank", "op": ">=", "value": 4}, PROFILE) == "pass"
    # German listed but no CEFR → unknown, not fail
    assert evaluate({"field": "languages['de'].cefr_rank", "op": ">=", "value": 3}, PROFILE) == "unknown"
    # French not listed at all → unknown
    assert evaluate({"field": "languages['fr'].cefr_rank", "op": ">=", "value": 3}, PROFILE) == "unknown"


def test_missing_field_is_unknown_never_fail():
    profile = {"derived": {}}
    assert evaluate({"field": "derived.total_experience_months", "op": ">=", "value": 36}, profile) == "unknown"
    assert evaluate({"field": "location.city_canonical", "op": "==", "value": "Ankara"}, profile) == "unknown"


def test_exists():
    assert evaluate({"field": "skills.canonical", "op": "exists"}, PROFILE) == "pass"
    assert evaluate({"field": "certifications.name_canonical", "op": "exists"}, PROFILE) == "fail"


def test_alias_education_rank():
    assert evaluate({"field": "education.highest_degree_rank", "op": ">=", "value": 3}, PROFILE) == "pass"


def test_kleene_composition():
    unknown_leaf = {"field": "languages['fr'].cefr_rank", "op": ">=", "value": 3}
    passing_leaf = {"field": "derived.job_changes_last_5y", "op": "<=", "value": 3}
    failing_leaf = {"field": "derived.job_changes_last_5y", "op": ">", "value": 3}
    assert evaluate({"all": [passing_leaf, unknown_leaf]}, PROFILE) == "unknown"
    assert evaluate({"all": [failing_leaf, unknown_leaf]}, PROFILE) == "fail"
    assert evaluate({"any": [passing_leaf, unknown_leaf]}, PROFILE) == "pass"
    assert evaluate({"any": [failing_leaf, unknown_leaf]}, PROFILE) == "unknown"
    assert evaluate({"not": passing_leaf}, PROFILE) == "fail"
    assert evaluate({"not": unknown_leaf}, PROFILE) == "unknown"


def test_unregistered_field_raises():
    with pytest.raises(PredicateError):
        evaluate({"field": "identity.full_name", "op": "exists"}, PROFILE)


def test_borderline_tolerance():
    profile = {"derived": {"total_experience_months": 34}}
    pred = {"field": "derived.total_experience_months", "op": ">=", "value": 36}
    strict, borderline = evaluate_with_borderline(pred, profile, 0.1)
    assert strict == "fail" and borderline is True  # 34 ≥ 36×0.9=32.4
    strict, borderline = evaluate_with_borderline(pred, {"derived": {"total_experience_months": 20}}, 0.1)
    assert strict == "fail" and borderline is False


def test_resolve_field_reports_presence():
    value, present = resolve_field(PROFILE, "derived.employment_gap_count")
    assert (value, present) == (1, True)
    _, present = resolve_field({"derived": {}}, "industries")
    assert present is False
