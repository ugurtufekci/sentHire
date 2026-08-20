"""Comparability: what a number on a requirement means, and when two are equal.

The claim under test is narrow and important: a difference in a candidate's
score must trace to a difference in a *judgment*, not to sampling noise in a
model's freehand decimal. These tests hold the line from both sides — scores
must snap onto the ladder, and near-identical totals must be presented as
equivalent rather than as a rank.
"""

import json

import pytest

from senthire.domain import anchors
from senthire.domain.ranking import EQUIVALENCE_EPSILON, equivalence_groups
from senthire.domain.scoring import RequirementVerdict, score
from senthire.domain.spec import EvaluationSpec, Requirement
from senthire.screening.assemble import merge_verdicts
from senthire.screening.deterministic import semantic_requirements


def _requirement(**overrides) -> Requirement:
    payload = {
        "req_id": "R1",
        "category": "skills",
        "label": {"tr": "Alan deneyimi"},
        "type": "scored",
        "importance": "high",
        "evaluator": "semantic",
        "semantic": {"rubric": "Judge depth."},
    }
    payload.update(overrides)
    return Requirement.model_validate(payload)


def _spec(*requirements: Requirement) -> EvaluationSpec:
    return EvaluationSpec.model_validate(
        {"weights": {"skills": 1.0}, "requirements": [r.model_dump() for r in requirements]}
    )


# --------------------------------------------------------------------------- #
# 1. The ladder
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "snapped"),
    [(1.0, 1.0), (0.92, 1.0), (0.72, 0.75), (0.68, 0.75), (0.5, 0.5), (0.3, 0.25), (0.04, 0.0)],
)
def test_scores_snap_to_the_default_ladder(raw, snapped):
    assert anchors.snap(_requirement(), raw) == snapped


def test_two_freehand_scores_on_the_same_rung_become_identical():
    """0.72 and 0.68 are the same judgment; presenting them as an order is the
    bug this whole mechanism exists to remove."""
    requirement = _requirement()
    assert anchors.snap(requirement, 0.72) == anchors.snap(requirement, 0.68)


def test_a_tie_between_rungs_rounds_down():
    # Exactly between 0.0 and 0.25: the candidate has not shown the higher rung.
    assert anchors.snap(_requirement(), 0.125) == 0.0
    assert anchors.snap(_requirement(), 0.875) == 0.75


def test_unknown_has_no_score_to_snap():
    assert anchors.snap(_requirement(), None) is None


def test_a_requirement_can_carry_its_own_ladder():
    requirement = _requirement(
        semantic={
            "rubric": "Kaç yıl?",
            "anchors": [
                {"score": 1.0, "label": {"tr": "5+ yıl"}, "definition": "beş yıl ve üzeri"},
                {"score": 0.5, "label": {"tr": "3–5 yıl"}, "definition": "üç ila beş yıl"},
                {"score": 0.0, "label": {"tr": "3 yıldan az"}, "definition": "üç yıldan az"},
            ],
        }
    )
    assert anchors.rungs(requirement) == [1.0, 0.5, 0.0]
    assert anchors.snap(requirement, 0.7) == 0.5
    assert anchors.rung_label(requirement, 0.5) == "3–5 yıl"


def test_the_model_is_shown_the_ladder_it_must_choose_from():
    payload = semantic_requirements(_spec(_requirement()))
    scale = payload[0]["scale"]
    assert [rung["score"] for rung in scale] == [1.0, 0.75, 0.5, 0.25, 0.0]
    assert all(rung["means"] for rung in scale), "a rung without a definition is a number"


def test_merging_puts_judged_scores_on_the_ladder_and_leaves_arithmetic_alone():
    spec = _spec(
        _requirement(),
        _requirement(
            req_id="R2", evaluator="deterministic", semantic=None,
            deterministic={"predicate": {"field": "derived.job_count", "op": ">=", "value": 2}},
        ),
    )
    judged = {"R1": RequirementVerdict(req_id="R1", verdict="partially_met", score=0.68,
                                       source_stage="light")}
    computed = {"R2": RequirementVerdict(req_id="R2", verdict="partially_met", score=0.61,
                                         source_stage="deterministic")}
    merged = merge_verdicts(spec, computed, light=judged)

    assert merged["R1"].score == 0.75, "a judgment lands on a rung"
    assert merged["R2"].score == 0.61, (
        "a computed score is exact — snapping it to a judgment ladder would "
        "throw away real information, such as borderline partial credit"
    )


def test_snapping_changes_the_final_score_in_the_direction_of_the_rung():
    spec = _spec(_requirement())
    lower = score(spec, {"R1": RequirementVerdict(req_id="R1", verdict="partially_met",
                                                  score=0.5, confidence=1.0, source_stage="light")})
    upper = score(spec, {"R1": RequirementVerdict(req_id="R1", verdict="partially_met",
                                                  score=0.75, confidence=1.0, source_stage="light")})
    assert upper.final_score > lower.final_score


# --------------------------------------------------------------------------- #
# 2. Equivalence
# --------------------------------------------------------------------------- #


def test_candidates_within_a_point_share_an_equivalence_group():
    assert equivalence_groups([100.0, 100.0, 80.5, 79.7, 73.6, 57.6]) == [0, 0, 1, 1, 2, 3]


def test_a_real_gap_starts_a_new_group():
    scores = [90.0, 90.0 - EQUIVALENCE_EPSILON]
    assert equivalence_groups(scores) == [0, 1]


def test_grouping_never_reorders_anyone():
    scores = [95.0, 94.5, 94.0, 60.0]
    groups = equivalence_groups(scores)
    assert groups == sorted(groups), "groups follow the ranking, they do not rewrite it"


def test_missing_scores_do_not_crash_the_grouping():
    assert equivalence_groups([None, None, 40.0]) == [0, 0, 1]


# --------------------------------------------------------------------------- #
# 3. Which criteria did any work
# --------------------------------------------------------------------------- #


def test_a_criterion_that_puts_everyone_on_one_rung_is_flagged():
    spec = _spec(_requirement(), _requirement(req_id="R2"))
    cohort = [
        {"R1": {"verdict": "met", "score": 1.0}, "R2": {"verdict": "met", "score": 1.0}},
        {"R1": {"verdict": "met", "score": 1.0}, "R2": {"verdict": "partially_met", "score": 0.5}},
        {"R1": {"verdict": "met", "score": 1.0}, "R2": {"verdict": "partially_met", "score": 0.25}},
    ]
    rows = {r["req_id"]: r for r in anchors.discrimination_report(spec, cohort)}
    assert rows["R1"]["flag"] == "no_discrimination"
    assert rows["R2"]["flag"] is None
    assert rows["R2"]["distinct_levels"] == 3


def test_a_criterion_nobody_had_information_for_is_flagged_separately():
    spec = _spec(_requirement())
    cohort = [{"R1": {"verdict": "unknown", "score": None}} for _ in range(3)]
    row = anchors.discrimination_report(spec, cohort)[0]
    assert row["flag"] == "all_unknown"
    assert row["unknown"] == 3


def test_two_candidates_are_not_enough_to_call_a_criterion_useless():
    spec = _spec(_requirement())
    cohort = [{"R1": {"verdict": "met", "score": 1.0}} for _ in range(2)]
    assert anchors.discrimination_report(spec, cohort)[0]["flag"] is None


# --------------------------------------------------------------------------- #
# 4. The shipped golden case stays anchored
# --------------------------------------------------------------------------- #


def test_every_shipped_semantic_score_sits_on_its_ladder():
    """A golden label off the ladder would quietly assert a score the pipeline
    can no longer produce."""
    from pathlib import Path

    root = Path("goldens/cases")
    for case_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        spec = EvaluationSpec.model_validate(
            json.loads((case_dir / "spec.json").read_text(encoding="utf-8"))
        )
        for candidate_file in sorted((case_dir / "candidates").glob("*.json")):
            payload = json.loads(candidate_file.read_text(encoding="utf-8"))
            for req_id, label in payload["labels"]["semantic"].items():
                raw = label.get("score")
                if raw is None:
                    continue
                requirement = spec.by_id(req_id)
                assert anchors.snap(requirement, raw) == pytest.approx(raw), (
                    f"{candidate_file.name}: {req_id} score {raw} is between rungs "
                    f"{anchors.rungs(requirement)}"
                )
