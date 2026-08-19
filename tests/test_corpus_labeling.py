"""The corpus pipeline: de-identify → label → adjudicate → promote → gate.

The tests that matter most here are the ones proving the *invariants have
teeth*: an assertion suite that cannot fail is worse than none, because it
reads like coverage. So several tests deliberately break a property and demand
that the runner notices.
"""

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from senthire.domain.spec import EvaluationSpec
from senthire.evals import autolabel, deidentify, invariants
from senthire.evals.corpus import Pool, make_case, read_json
from senthire.evals.fake_oracle import deterministic_oracle
from senthire.evals.loader import load_case, load_cases
from senthire.evals.promote import promote
from senthire.evals.runner import run_case
from senthire.screening.schemas import EvidenceQuote, ReqJudgment

GOLDEN_CASE = Path("goldens/cases/b2b-sales-ankara")
AS_OF = date(2026, 8, 1)
SALT = "test-salt"


@pytest.fixture
def spec() -> EvaluationSpec:
    return EvaluationSpec.model_validate(read_json(GOLDEN_CASE / "spec.json"))


@pytest.fixture
def profiles() -> list[dict]:
    return [
        read_json(p)["profile"] for p in sorted((GOLDEN_CASE / "candidates").glob("*.json"))
    ]


@pytest.fixture
def pool(tmp_path, spec, profiles) -> Pool:
    pool = Pool(tmp_path / "corpus", "test-pool")
    for index, profile in enumerate(profiles):
        pool.add(
            make_case(profile, salt=SALT, seed=f"seed-{index}", imported_at=AS_OF,
                      text="B2B satış deneyimi, kota sorumluluğu, Ankara.")
        )
    pool.save_spec("job", spec)
    return pool


# --------------------------------------------------------------------------- #
# 1. De-identification
# --------------------------------------------------------------------------- #


def test_deidentification_removes_identity_but_keeps_the_signal():
    profile = {
        "identity": {
            "full_name": "Ayşe Demir",
            "emails": ["ayse.demir@gercek-sirket.com"],
            "phones": ["+90 532 111 22 33"],
            "links": [{"type": "linkedin", "url": "https://linkedin.com/in/aysedemir"}],
        },
        "experience": [{"title_raw": "Satış Uzmanı", "company": "Örnek A.Ş."}],
        "location": {"city_canonical": "Ankara"},
    }
    result = deidentify.deidentify_profile(profile, salt=SALT, seed="doc-1")

    identity = result.profile["identity"]
    assert identity["full_name"] != "Ayşe Demir"
    assert "gercek-sirket.com" not in json.dumps(result.profile)
    assert identity["phones"] != ["+90 532 111 22 33"]
    assert identity["links"] == [], "profile URLs identify as surely as a name"
    # ...and everything screening reasons about survives untouched
    assert result.profile["experience"][0]["title_raw"] == "Satış Uzmanı"
    assert result.profile["location"]["city_canonical"] == "Ankara"


def test_pseudonyms_are_stable_per_person_and_differ_between_people():
    profile = {"identity": {"full_name": "Ayşe Demir", "emails": [], "phones": []}}
    once = deidentify.deidentify_profile(profile, salt=SALT, seed="doc-1")
    twice = deidentify.deidentify_profile(profile, salt=SALT, seed="doc-1")
    other = deidentify.deidentify_profile(profile, salt=SALT, seed="doc-2")
    assert once.profile["identity"]["full_name"] == twice.profile["identity"]["full_name"]
    assert once.profile["identity"]["full_name"] != other.profile["identity"]["full_name"]
    # A different salt must move everyone: the salt is what makes the map secret.
    salted = deidentify.deidentify_profile(profile, salt="other-salt", seed="doc-1")
    assert salted.profile["identity"]["full_name"] != once.profile["identity"]["full_name"]


def test_raw_text_is_scrubbed_of_contact_details_and_id_numbers():
    text = (
        "Ayşe Demir\nayse.demir@gercek-sirket.com\n+90 532 111 22 33\n"
        "TC: 12345678901\nDoğum: 01.05.1990\nB2B satış deneyimi."
    )
    scrubbed = deidentify.scrub_text(text, {"Ayşe Demir": "Elif Kaya"})
    assert "Ayşe Demir" not in scrubbed
    assert "gercek-sirket.com" not in scrubbed
    assert "12345678901" not in scrubbed
    assert "01.05.1990" not in scrubbed
    assert "B2B satış deneyimi." in scrubbed, "the screenable content must survive"


def test_a_corpus_case_never_carries_the_re_identification_map(pool):
    stored = json.dumps([c.model_dump(mode="json") for c in pool.cases()])
    assert "replacements" not in stored


# --------------------------------------------------------------------------- #
# 2. Invariants — ground truth without a labeler
# --------------------------------------------------------------------------- #


def test_every_case_gets_a_fairness_twin_with_a_flipped_coding(pool):
    for case in pool.cases():
        twin = invariants.fairness_twin(case, salt=SALT)
        assert twin is not None
        assert twin.assertion == "equal"
        assert twin.profile["identity"]["full_name"] != case.profile["identity"]["full_name"]
        assert twin.detail["to_class"] != twin.detail["from_class"]
        # Only identity moves — anything else would make the twin meaningless.
        stripped = {k: v for k, v in twin.profile.items() if k != "identity"}
        assert stripped == {k: v for k, v in case.profile.items() if k != "identity"}


def test_more_experience_twin_declines_when_a_rule_rewards_less(pool, spec):
    case = pool.cases()[0]
    assert invariants.more_experience_twin(case, spec) is not None

    capped = spec.model_copy(deep=True)
    capped.requirements[0].deterministic.predicate = {
        "field": "derived.total_experience_months", "op": "<=", "value": 120
    }
    assert invariants.more_experience_twin(case, capped) is None, (
        "an upper bound makes monotonicity arguable — decline rather than assert"
    )


def test_knockout_twins_actually_violate_the_rule_they_name(pool, spec):
    from senthire.domain.scoring import RequirementVerdict
    from senthire.evals.runner import evaluate_profile

    case = pool.cases()[0]
    twins = invariants.knockout_twins(case, spec)
    assert twins, "the spec has hard deterministic rules; twins must be generated"
    for twin in twins:
        outcome = evaluate_profile(spec, twin.profile, {}, AS_OF)
        assert outcome.gate == "fail"
        assert twin.detail["req_id"] in outcome.knockouts
    assert RequirementVerdict  # imported for the signature above


def test_weaker_language_twin_only_touches_a_language_the_spec_asks_about(pool, spec):
    case = next(c for c in pool.cases() if c.profile.get("languages"))
    twin = invariants.weaker_language_twin(case, spec)
    if twin is None:
        pytest.skip("this case has no downgradeable language")
    assert twin.assertion == "not_higher"
    assert twin.detail["language"] in {"en"}


# --------------------------------------------------------------------------- #
# 3. Ensemble aggregation
# --------------------------------------------------------------------------- #


def _judgment(verdict: str, *, confidence: float = 0.9, quote: str | None = None) -> ReqJudgment:
    return ReqJudgment(
        req_id="R1", verdict=verdict, score=1.0 if verdict == "met" else 0.0,
        confidence=confidence, info_status="explicit" if quote else "missing",
        evidence=[EvidenceQuote(quote=quote)] if quote else [],
        reasoning="because",
    )


def test_unanimous_votes_become_a_label_without_a_human():
    label = autolabel.aggregate([_judgment("met")] * 3)
    assert label.verdict == "met"
    assert label.agreement == 1.0
    assert not label.needs_adjudication


def test_a_met_versus_not_met_split_always_reaches_a_human():
    votes = [_judgment("met"), _judgment("met"), _judgment("not_met")]
    label = autolabel.aggregate(votes, min_agreement=0.6)
    assert label.needs_adjudication, (
        "2/3 agreement is enough arithmetic, but met-vs-not_met is a real dispute"
    )
    votes = [_judgment("met"), _judgment("met"), _judgment("partially_met")]
    assert not autolabel.aggregate(votes, min_agreement=0.6).needs_adjudication


def test_unanimous_unknown_is_a_confident_label():
    label = autolabel.aggregate([_judgment("unknown", confidence=0.4)] * 3)
    assert label.verdict == "unknown"
    assert label.confidence == 1.0, "'the CV does not say it' is a claim about the document"


def test_a_vote_resting_on_a_quote_that_is_not_in_the_cv_is_discarded(pool, spec):
    case = pool.cases()[0]
    real, invented = "B2B satış deneyimi", "on yıllık ekip yöneticiliği"

    def oracle(_spec, _profile, lens):
        from senthire.screening.schemas import LightScreenOutput

        quote = invented if lens == "advocate" else real
        return LightScreenOutput(
            judgments=[
                ReqJudgment(
                    req_id=req.req_id, verdict="met" if lens == "advocate" else "partially_met",
                    score=0.5, confidence=0.9, info_status="explicit",
                    evidence=[EvidenceQuote(quote=quote)], reasoning="…",
                )
                for req in spec.requirements
                if req.evaluator in {"semantic", "hybrid"}
            ]
        )

    report = autolabel.LabelingReport()
    labels = autolabel.label_case(
        spec, case, oracle=oracle, as_of=AS_OF, report=report
    )
    assert report.dropped_evidence > 0
    semantic = [r.req_id for r in spec.requirements if r.evaluator in {"semantic", "hybrid"}]
    assert all(labels[req_id].verdict == "partially_met" for req_id in semantic), (
        "the fabricated-evidence vote must not reach the label"
    )


def test_human_adjudication_overrides_the_ensemble(pool, spec):
    labels = {
        case.corpus_id: autolabel.label_case(
            spec, case, oracle=deterministic_oracle, as_of=AS_OF, min_agreement=1.0
        )
        for case in pool.cases()
    }
    disputed = [
        (cid, req_id) for cid, reqs in labels.items()
        for req_id, label in reqs.items() if label.needs_adjudication
    ]
    assert disputed, "the fake oracle is built to disagree somewhere"
    corpus_id, req_id = disputed[0]
    applied = autolabel.apply_adjudications(
        labels, [{"corpus_id": corpus_id, "req_id": req_id, "verdict": "met"}]
    )
    assert applied == 1
    resolved = labels[corpus_id][req_id]
    assert (resolved.verdict, resolved.source, resolved.needs_adjudication) == ("met", "human", False)


# --------------------------------------------------------------------------- #
# 4. Promotion, end to end
# --------------------------------------------------------------------------- #


def _label_and_promote(pool, tmp_path, **kwargs):
    from senthire.evals.corpus import LabelSet

    spec = pool.spec("job")
    label_set = LabelSet(pool=pool.name, job="job", spec_version=spec.version, labeled_at=AS_OF)
    for case in pool.cases():
        label_set.cases[case.corpus_id] = autolabel.label_case(
            spec, case, oracle=deterministic_oracle, as_of=AS_OF, min_agreement=1.0
        )
    # stand in for the reviewer: accept the leading verdict on every split
    autolabel.apply_adjudications(
        label_set.cases,
        [
            {"corpus_id": cid, "req_id": rid, "verdict": label_set.cases[cid][rid].verdict}
            for cid, rid in label_set.unresolved()
        ],
    )
    pool.save_labels(label_set)
    return promote(
        pool, "job", out_root=tmp_path / "goldens", case_name="auto", salt=SALT,
        as_of=AS_OF, **kwargs,
    )


def test_a_promoted_case_loads_and_passes_the_gate(pool, tmp_path):
    report = _label_and_promote(pool, tmp_path)
    assert report.promoted
    assert report.twins["fairness"] == len(report.promoted)
    assert not report.rejected_twins

    case = load_case(tmp_path / "goldens" / "auto")
    run = run_case(case)
    assert run.mismatch_count == 0, [o.mismatches for o in run.outcomes] + run.case_mismatches
    # every promoted CV brought at least one free assertion with it
    assert len(case.candidates) > len(report.promoted)
    assert case.expectations.fairness_pairs and case.expectations.monotonic_pairs


def test_promotion_skips_what_it_cannot_label_cleanly(pool, tmp_path):
    report = _label_and_promote(pool, tmp_path, min_confidence=1.01)
    assert report.skipped.get("low_confidence"), (
        "an unreachable confidence bar must skip cases, never lower the bar"
    )


def test_a_twin_whose_construction_failed_is_dropped_not_asserted(pool, tmp_path, monkeypatch):
    """If the edit did not actually violate the rule, the invariant is a lie."""
    real_knockout_twins = invariants.knockout_twins

    def broken(case, spec):
        twins = real_knockout_twins(case, spec)
        for twin in twins:
            # "violate" the rule by not violating it
            object.__setattr__(twin, "profile", case.profile)
        return twins

    monkeypatch.setattr("senthire.evals.promote.generate", lambda case, spec, *, salt: broken(case, spec))
    report = _label_and_promote(pool, tmp_path)
    assert report.rejected_twins, "a knockout twin that isn't knocked out must be reported"
    # Cases that were already gated out stay legitimately failing, so the count
    # drops rather than reaching zero — what matters is that no unverified
    # twin was written.
    assert report.twins["knockout"] < len(report.promoted)
    assert run_case(load_case(tmp_path / "goldens" / "auto")).mismatch_count == 0


# --------------------------------------------------------------------------- #
# 5. Do the assertions actually bite?
# --------------------------------------------------------------------------- #


@pytest.fixture
def editable_case(tmp_path) -> Path:
    target = tmp_path / "cases" / "b2b"
    shutil.copytree(GOLDEN_CASE, target)
    return target


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_a_fairness_pair_fails_when_the_twin_scores_differently(editable_case):
    twin = read_json(editable_case / "candidates" / "c01-strong-match.json")
    twin["golden_id"] = "c01-twin"
    twin["variant_of"] = "c01-strong-match"
    # the "same" candidate, judged one notch worse — exactly the bias we hunt
    twin["labels"]["semantic"]["R3_sales_depth"]["verdict"] = "partially_met"
    twin["labels"]["semantic"]["R3_sales_depth"]["score"] = 0.5
    _write(editable_case / "candidates" / "c01-twin.json", twin)

    expectations = read_json(editable_case / "expectations.json")
    expectations["fairness_pairs"] = [["c01-strong-match", "c01-twin"]]
    _write(editable_case / "expectations.json", expectations)

    report = run_case(load_case(editable_case))
    assert any("fairness" in m for m in report.case_mismatches), report.case_mismatches


def test_a_monotonic_pair_fails_when_the_order_inverts(editable_case):
    expectations = read_json(editable_case / "expectations.json")
    # c06 is knocked out on experience; asserting it outscores the best
    # candidate is exactly the kind of inversion the check exists to catch.
    expectations["monotonic_pairs"] = [["c06-junior-knockout", "c01-strong-match"]]
    _write(editable_case / "expectations.json", expectations)

    report = run_case(load_case(editable_case))
    assert any("monotonicity" in m for m in report.case_mismatches), report.case_mismatches


def test_monotonic_pairs_referencing_unknown_ids_are_rejected_at_load(editable_case):
    expectations = read_json(editable_case / "expectations.json")
    expectations["monotonic_pairs"] = [["c01-strong-match", "does-not-exist"]]
    _write(editable_case / "expectations.json", expectations)
    with pytest.raises(Exception, match="monotonic_pairs"):
        load_cases(editable_case.parent)
