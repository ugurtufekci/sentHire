"""Borderline self-consistency voting (docs/07 §7): majority wins, splits go
to a human, evidence discipline applies to votes, and the temperature ladder
is explicit."""

import uuid
from types import SimpleNamespace

import pytest

from senthire.config import Settings
from senthire.screening.llm import LlmUsage, ScreeningCallFailed
from senthire.screening.schemas import (
    DeepAnalysisOutput,
    EvidenceQuote,
    ReqJudgment,
)
from senthire.screening.voting import (
    UNCERTAINTY_REASONS,
    deep_vote,
    vote_count,
)

RAW_TEXT = "Sekiz yil Python deneyimi. Kurumsal musterilere B2B satis yapti."


def judgment(req_id="R1", verdict="met", score=1.0, confidence=0.9, quote=None):
    if quote is None:
        quote = "Sekiz yil Python deneyimi"
    return ReqJudgment(
        req_id=req_id,
        verdict=verdict,
        score=score,
        confidence=confidence,
        info_status="explicit" if verdict != "unknown" else "missing",
        evidence=[] if verdict == "unknown" else [EvidenceQuote(quote=quote)],
        reasoning="vote",
    )


def output(*judgments, summary="ozet"):
    return DeepAnalysisOutput(judgments=list(judgments), summary=summary)


def usage():
    return LlmUsage("fake-deep", 100, 40, 0, 0)


def scripted(monkeypatch, outputs, *, fail_at=None):
    """Make deep_vote's analyze calls return the scripted outputs in order,
    recording each call's temperature."""
    temperatures = []
    calls = {"n": 0}

    def fake_analyze(spec, profile, raw_text, light_judgments, *, temperature=None):
        index = calls["n"]
        calls["n"] += 1
        temperatures.append(temperature)
        if fail_at is not None and index in fail_at:
            raise ScreeningCallFailed("permanent 400")
        return outputs[index], usage()

    return fake_analyze, temperatures, calls


SPEC = SimpleNamespace()  # deep_vote never inspects the spec itself


# --------------------------------------------------------------------------- #
# vote_count: who earns extra votes
# --------------------------------------------------------------------------- #


def test_only_uncertainty_reasons_earn_votes():
    assert vote_count([]) == 1
    assert vote_count(None) == 1
    assert vote_count(["decision_band"]) == 1
    assert vote_count(["decision_band", "borderline_hard_filter"]) == 3
    assert vote_count(["hard_requirement_unverified"]) == 3
    assert vote_count(["low_confidence_on_heavy_requirement"]) == 3


def test_votes_can_be_disabled(monkeypatch):
    import senthire.screening.voting as voting

    monkeypatch.setattr(
        voting, "get_settings", lambda: Settings(_env_file=None, deep_borderline_votes=1)
    )
    assert vote_count(["borderline_hard_filter"]) == 1


def test_uncertainty_reasons_still_exist_in_selection_policy():
    """If selection renames a reason, voting must not silently stop firing."""
    import inspect

    from senthire.screening import selection

    source = inspect.getsource(selection)
    for reason in UNCERTAINTY_REASONS:
        assert f'"{reason}"' in source, f"selection no longer emits {reason}"


# --------------------------------------------------------------------------- #
# deep_vote: merging
# --------------------------------------------------------------------------- #


def test_unanimous_votes_merge_clean(monkeypatch):
    votes = [output(judgment()), output(judgment()), output(judgment())]
    analyze, temperatures, _ = scripted(monkeypatch, votes)

    merged, usages, meta = deep_vote(SPEC, {}, RAW_TEXT, [], votes=3, analyze=analyze)

    assert [j.verdict for j in merged.judgments] == ["met"]
    assert merged.judgments[0].confidence == pytest.approx(0.9)  # 0.9 mean * 1.0 agreement
    assert merged.summary == "ozet"  # narrative comes from the first pass
    assert len(usages) == 3
    assert meta["completed"] == 3 and meta["vote_errors"] == 0
    assert meta["flagged"] == []
    assert meta["per_requirement"]["R1"] == {"votes": {"met": 3}, "agreement": 1.0}
    # vote 1 at the judge default (None -> settings), later votes sample hot
    assert temperatures[0] is None
    assert temperatures[1:] == [Settings(_env_file=None).deep_vote_temperature] * 2


def test_adjacent_majority_wins_without_flag(monkeypatch):
    votes = [
        output(judgment(verdict="met", score=1.0)),
        output(judgment(verdict="met", score=1.0)),
        output(judgment(verdict="partially_met", score=0.5)),
    ]
    analyze, _, _ = scripted(monkeypatch, votes)

    merged, _, meta = deep_vote(SPEC, {}, RAW_TEXT, [], votes=3, analyze=analyze)

    assert merged.judgments[0].verdict == "met"
    assert meta["flagged"] == [], "an adjacent 2v1 is sampling noise, not a review case"
    assert meta["per_requirement"]["R1"]["votes"] == {"met": 2, "partially_met": 1}
    assert merged.judgments[0].confidence == pytest.approx(round(0.9 * 2 / 3, 3))


def test_met_vs_not_met_split_is_flagged_even_with_majority(monkeypatch):
    votes = [
        output(judgment(verdict="met")),
        output(judgment(verdict="met")),
        output(judgment(verdict="not_met", score=0.0)),
    ]
    analyze, _, _ = scripted(monkeypatch, votes)

    merged, _, meta = deep_vote(SPEC, {}, RAW_TEXT, [], votes=3, analyze=analyze)

    assert merged.judgments[0].verdict == "met"
    assert meta["flagged"] == ["R1"], "a met-vs-not_met pool always reaches a human"


def test_no_majority_keeps_first_pass_and_flags(monkeypatch):
    first = judgment(verdict="met", score=1.0)
    votes = [
        output(first),
        output(judgment(verdict="partially_met", score=0.5)),
        output(judgment(verdict="not_met", score=0.0)),
    ]
    analyze, _, _ = scripted(monkeypatch, votes)

    merged, _, meta = deep_vote(SPEC, {}, RAW_TEXT, [], votes=3, analyze=analyze)

    assert merged.judgments[0].verdict == "met"  # the first pass, unaveraged
    assert merged.judgments[0].confidence == pytest.approx(0.9)  # untempered — flagged instead
    assert meta["flagged"] == ["R1"]


def test_median_score_rounds_down_among_winners(monkeypatch):
    votes = [
        output(judgment(verdict="met", score=1.0)),
        output(judgment(verdict="met", score=0.75)),
        output(judgment(verdict="partially_met", score=0.5)),
    ]
    analyze, _, _ = scripted(monkeypatch, votes)

    merged, _, _ = deep_vote(SPEC, {}, RAW_TEXT, [], votes=3, analyze=analyze)

    assert merged.judgments[0].score == 0.75, "even winner pools take the lower median"


def test_vote_with_fabricated_evidence_is_excluded(monkeypatch):
    votes = [
        output(judgment(verdict="met")),
        output(judgment(verdict="partially_met", score=0.5, quote="On yil Java tecrubesi")),
        output(judgment(verdict="partially_met", score=0.5, quote="On yil Java tecrubesi")),
    ]
    analyze, _, _ = scripted(monkeypatch, votes)

    merged, _, meta = deep_vote(SPEC, {}, RAW_TEXT, [], votes=3, analyze=analyze)

    assert merged.judgments[0].verdict == "met", "votes resting on invented quotes don't count"
    assert meta["per_requirement"]["R1"]["excluded"] == 2
    assert meta["per_requirement"]["R1"]["agreement"] == 1.0
    assert meta["flagged"] == []


def test_all_evidence_failed_falls_back_to_first_and_flags(monkeypatch):
    bad = "Bu cumle CV'de yok"
    votes = [
        output(judgment(verdict="met", quote=bad)),
        output(judgment(verdict="met", quote=bad)),
        output(judgment(verdict="met", quote=bad)),
    ]
    analyze, _, _ = scripted(monkeypatch, votes)

    merged, _, meta = deep_vote(SPEC, {}, RAW_TEXT, [], votes=3, analyze=analyze)

    assert merged.judgments[0].verdict == "met"  # kept, but…
    assert meta["flagged"] == ["R1"]  # …a human sees it
    assert meta["per_requirement"]["R1"] == {"votes": {}, "agreement": 0.0, "excluded": 3}


def test_unknown_votes_never_touch_evidence_check(monkeypatch):
    votes = [
        output(judgment(verdict="unknown", score=None)),
        output(judgment(verdict="unknown", score=None)),
        output(judgment(verdict="unknown", score=None)),
    ]
    analyze, _, _ = scripted(monkeypatch, votes)

    merged, _, meta = deep_vote(SPEC, {}, RAW_TEXT, [], votes=3, analyze=analyze)

    assert merged.judgments[0].verdict == "unknown"
    assert meta["flagged"] == []
    assert meta["per_requirement"]["R1"]["agreement"] == 1.0


def test_requirements_missing_from_some_votes_still_merge(monkeypatch):
    votes = [
        output(judgment("R1"), judgment("R2", verdict="partially_met", score=0.5)),
        output(judgment("R1")),
        output(judgment("R1"), judgment("R2", verdict="partially_met", score=0.5)),
    ]
    analyze, _, _ = scripted(monkeypatch, votes)

    merged, _, meta = deep_vote(SPEC, {}, RAW_TEXT, [], votes=3, analyze=analyze)

    verdicts = {j.req_id: j.verdict for j in merged.judgments}
    assert verdicts == {"R1": "met", "R2": "partially_met"}
    assert meta["per_requirement"]["R2"]["votes"] == {"partially_met": 2}


# --------------------------------------------------------------------------- #
# deep_vote: failure behavior
# --------------------------------------------------------------------------- #


def test_first_vote_failure_propagates(monkeypatch):
    analyze, _, _ = scripted(monkeypatch, [output(judgment())] * 3, fail_at={0})
    with pytest.raises(ScreeningCallFailed):
        deep_vote(SPEC, {}, RAW_TEXT, [], votes=3, analyze=analyze)


def test_later_vote_failure_degrades_the_pool(monkeypatch):
    votes = [output(judgment()), output(judgment()), output(judgment())]
    analyze, _, calls = scripted(monkeypatch, votes, fail_at={2})

    merged, usages, meta = deep_vote(SPEC, {}, RAW_TEXT, [], votes=3, analyze=analyze)

    assert calls["n"] == 3
    assert meta["completed"] == 2 and meta["vote_errors"] == 1
    assert len(usages) == 2
    assert merged.judgments[0].verdict == "met"


# --------------------------------------------------------------------------- #
# persist wiring: disagreement reaches the recruiter
# --------------------------------------------------------------------------- #


def _small_spec():
    from senthire.domain.spec import (
        DeterministicCheck,
        EvaluationSpec,
        Requirement,
        SemanticCheck,
    )

    return EvaluationSpec(
        requirements=[
            Requirement(
                req_id="H1", category="relevant_experience", type="hard",
                evaluator="deterministic",
                deterministic=DeterministicCheck(
                    predicate={"field": "derived.total_experience_months",
                               "op": ">=", "value": 36}
                ),
            ),
            Requirement(
                req_id="R1", category="relevant_experience", type="scored",
                evaluator="semantic", semantic=SemanticCheck(rubric="depth"),
            ),
            Requirement(
                req_id="R2", category="skills", type="scored",
                evaluator="semantic", semantic=SemanticCheck(rubric="crm"),
            ),
        ]
    )


def _light_pair(r1, r2):
    def one(req_id, pair):
        verdict, score = pair
        return judgment(req_id, verdict=verdict, score=score,
                        quote="Sekiz yil Python deneyimi")

    return [one("R1", r1), one("R2", r2)]


def _persist_fixture():
    from senthire.domain.scoring import score as run_scorer
    from senthire.screening.assemble import (
        build_result_document,
        judgments_to_verdicts,
        merge_verdicts,
    )
    from senthire.screening.deterministic import run_deterministic_stage

    spec = _small_spec()
    profile = {"derived": {"total_experience_months": 48}}
    out = SimpleNamespace(judgments=_light_pair(("met", 1.0), ("partially_met", 0.5)))
    det = run_deterministic_stage(spec, profile)
    light_verdicts = judgments_to_verdicts(out.judgments, "light")
    verdicts = merge_verdicts(spec, det.verdicts, light_verdicts)
    sr = run_scorer(spec, verdicts)
    doc = build_result_document(spec, verdicts, sr, stage_reached="light")

    ev = SimpleNamespace(
        result=doc, application_id=uuid.uuid4(), models_used={},
        stage_reached="light", hard_result="pass",
        overall_score=sr.final_score, confidence=sr.confidence,
    )
    profile_row = SimpleNamespace(profile=profile, raw_text=RAW_TEXT)
    run = SimpleNamespace(funnel={"deep_reasons": {}}, org_id=None, id=None, mode="interactive")
    return spec, ev, profile_row, run


def test_vote_disagreement_lands_in_review_reasons():
    from senthire.workers.tasks.screen import _persist_deep_evaluation

    spec, ev, profile_row, run = _persist_fixture()
    deep_out = DeepAnalysisOutput(
        judgments=_light_pair(("met", 1.0), ("partially_met", 0.5)),
        summary="derin ozet",
    )
    vote_meta = {"requested": 3, "completed": 3, "vote_errors": 0,
                 "flagged": ["R2"], "per_requirement": {}}

    _persist_deep_evaluation(
        None, run, spec, ev, profile_row, deep_out, None, vote_meta=vote_meta
    )

    assert ev.result["deep_votes"] == vote_meta
    assert ev.result["needs_review"] is True
    assert "deep_vote_disagreement" in ev.result["review_reasons"]
    assert ev.stage_reached == "deep"


def test_clean_votes_add_provenance_without_review():
    from senthire.workers.tasks.screen import _persist_deep_evaluation

    spec, ev, profile_row, run = _persist_fixture()
    deep_out = DeepAnalysisOutput(
        judgments=_light_pair(("met", 1.0), ("partially_met", 0.5)),
        summary="derin ozet",
    )
    vote_meta = {"requested": 3, "completed": 3, "vote_errors": 0,
                 "flagged": [], "per_requirement": {"R1": {"agreement": 1.0}}}

    _persist_deep_evaluation(
        None, run, spec, ev, profile_row, deep_out, None, vote_meta=vote_meta
    )

    assert ev.result["deep_votes"]["per_requirement"]["R1"]["agreement"] == 1.0
    assert "deep_vote_disagreement" not in ev.result.get("review_reasons", [])


# --------------------------------------------------------------------------- #
# temperature plumbing
# --------------------------------------------------------------------------- #


class _StubResponse:
    def __init__(self):
        self.parsed_output = {"ok": True}
        self.usage = SimpleNamespace(
            input_tokens=10, output_tokens=5,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
        )


def test_call_model_always_sends_an_explicit_temperature(monkeypatch):
    import senthire.screening.llm as llm

    captured = {}

    class _StubClient:
        def __init__(self):
            self.messages = SimpleNamespace(parse=self._parse)

        def _parse(self, **kwargs):
            captured.update(kwargs)
            return _StubResponse()

    monkeypatch.setattr(llm.anthropic, "Anthropic", _StubClient)

    llm.call_model("m", "sys", [], dict, 100)
    assert captured["temperature"] == Settings(_env_file=None).judge_temperature

    llm.call_model("m", "sys", [], dict, 100, temperature=1.0)
    assert captured["temperature"] == 1.0


def test_batch_requests_carry_the_judge_temperature():
    from senthire.screening.batch import deep_request, light_request

    spec = _small_spec()
    expected = Settings(_env_file=None).judge_temperature
    assert light_request("c1", spec, {})["params"]["temperature"] == expected
    assert deep_request("c2", spec, {}, "raw", [])["params"]["temperature"] == expected


def test_extractor_sends_the_judge_temperature(monkeypatch):
    import senthire.extraction.extractor as extractor

    captured = {}

    class _StubClient:
        def __init__(self):
            self.messages = SimpleNamespace(parse=self._parse)

        def _parse(self, **kwargs):
            captured.update(kwargs)
            response = _StubResponse()
            response.parsed_output = None  # force the explicit failure path
            return response

    monkeypatch.setattr(extractor, "_client", lambda: _StubClient())
    with pytest.raises(extractor.ExtractionFailed):
        extractor._parse([{"role": "user", "content": "x"}], "m")
    assert captured["temperature"] == Settings(_env_file=None).judge_temperature


def test_compiler_sends_the_judge_temperature(monkeypatch):
    import senthire.compiler.compiler as compiler

    captured = {}

    class _StubClient:
        def __init__(self):
            self.messages = SimpleNamespace(parse=self._parse)

        def _parse(self, **kwargs):
            captured.update(kwargs)
            response = _StubResponse()
            response.parsed_output = None  # force the explicit failure path
            return response

    monkeypatch.setattr(compiler.anthropic, "Anthropic", _StubClient)
    with pytest.raises(compiler.CompilationFailed):
        compiler.compile_spec(None, "En az 3 yil deneyim.", version=1, locale="tr")
    assert captured["temperature"] == Settings(_env_file=None).judge_temperature
