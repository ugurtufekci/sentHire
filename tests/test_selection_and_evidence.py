from senthire.domain.scoring import RequirementVerdict, score
from senthire.domain.spec import EvaluationSpec, Requirement, SemanticCheck
from senthire.screening.evidence import verify_all, verify_judgment
from senthire.screening.schemas import EvidenceQuote, ReqJudgment
from senthire.screening.selection import Preliminary, select_for_deep

# --------------------------------------------------------------------------- #
# selection policy
# --------------------------------------------------------------------------- #


def _spec() -> EvaluationSpec:
    return EvaluationSpec(
        weights={"skills": 1.0},
        requirements=[
            Requirement(
                req_id="S1", category="skills", type="scored", evaluator="semantic",
                semantic=SemanticCheck(rubric="…"),
            )
        ],
    )


def _prelim(app_id: str, s: float, conf: float = 0.9, borderline: bool = False) -> Preliminary:
    spec = _spec()
    verdicts = {
        "S1": RequirementVerdict(
            req_id="S1", verdict="met", score=s, confidence=conf, source_stage="light"
        )
    }
    return Preliminary(
        application_id=app_id,
        score_result=score(spec, verdicts),
        verdicts=verdicts,
        borderline=borderline,
    )


def _select(prelims, top_k=2, band_extra=1):
    return select_for_deep(
        _spec(), prelims, top_k=top_k, band_extra=band_extra,
        confidence_threshold=0.7, weight_threshold=0.1,
    )


def test_decision_band_selects_top_ranks_only():
    prelims = [_prelim(f"a{i}", s=1 - i * 0.1) for i in range(6)]
    selected = _select(prelims)  # band = ranks 1..3
    ids = {p.application_id for p in selected}
    assert ids == {"a0", "a1", "a2"}
    assert all("decision_band" in p.reasons for p in selected)


def test_low_confidence_on_heavy_requirement_selects():
    prelims = [_prelim(f"a{i}", s=1 - i * 0.1) for i in range(5)]
    prelims.append(_prelim("shaky", s=0.2, conf=0.4))  # far below band, but unsure
    selected = _select(prelims)
    shaky = next(p for p in selected if p.application_id == "shaky")
    assert "low_confidence_on_heavy_requirement" in shaky.reasons


def test_borderline_always_selected():
    prelims = [_prelim("a0", s=0.9), _prelim("edge", s=0.1, borderline=True)]
    selected = _select(prelims, top_k=1, band_extra=0)
    edge = next(p for p in selected if p.application_id == "edge")
    assert "borderline_hard_filter" in edge.reasons


def test_cap_bounds_uncertainty_but_never_the_band():
    """The 220-CV rehearsal sent the whole cohort deep when the light model
    hedged everywhere; the cap is the ceiling that prevents that bill."""
    prelims = [_prelim(f"a{i}", s=1 - i * 0.01, conf=0.4) for i in range(30)]
    selected = select_for_deep(
        _spec(), prelims, top_k=2, band_extra=1,
        confidence_threshold=0.7, weight_threshold=0.1, cap=5,
    )
    assert len(selected) == 5
    ids = {p.application_id for p in selected}
    assert {"a0", "a1", "a2"} <= ids, "the decision band always enters"
    # overflow slots go to the strongest uncertain candidates, deterministically
    assert ids == {"a0", "a1", "a2", "a3", "a4"}


def test_cap_prioritizes_a_possibly_wrong_knockout_over_hedging():
    prelims = [_prelim(f"a{i}", s=1 - i * 0.01, conf=0.4) for i in range(6)]
    prelims.append(_prelim("edge", s=0.05, borderline=True))
    selected = select_for_deep(
        _spec(), prelims, top_k=1, band_extra=0,
        confidence_threshold=0.7, weight_threshold=0.1, cap=2,
    )
    ids = {p.application_id for p in selected}
    assert ids == {"a0", "edge"}, (
        "with one overflow slot, the borderline knockout must win it"
    )


def test_cap_never_blocks_reason_accumulation_for_the_selected():
    prelims = [_prelim("a0", s=0.9, conf=0.3, borderline=True)]
    prelims += [_prelim(f"b{i}", s=0.5, conf=0.3) for i in range(5)]
    selected = select_for_deep(
        _spec(), prelims, top_k=1, band_extra=0,
        confidence_threshold=0.7, weight_threshold=0.1, cap=1,
    )
    a0 = next(p for p in selected if p.application_id == "a0")
    assert "decision_band" in a0.reasons
    assert "borderline_hard_filter" in a0.reasons
    assert "low_confidence_on_heavy_requirement" in a0.reasons
    assert len(selected) == 1


def test_no_cap_keeps_the_old_behavior():
    prelims = [_prelim(f"a{i}", s=1 - i * 0.01, conf=0.4) for i in range(30)]
    selected = select_for_deep(
        _spec(), prelims, top_k=2, band_extra=1,
        confidence_threshold=0.7, weight_threshold=0.1,
    )
    assert len(selected) == 30


def test_reasons_accumulate_without_duplication():
    prelims = [_prelim("a0", s=0.9, conf=0.3, borderline=True)]
    selected = _select(prelims, top_k=1, band_extra=0)
    assert len(selected) == 1
    reasons = selected[0].reasons
    assert len(reasons) == len(set(reasons))
    assert {"decision_band", "borderline_hard_filter", "low_confidence_on_heavy_requirement"} <= set(
        reasons
    )


# --------------------------------------------------------------------------- #
# evidence verification
# --------------------------------------------------------------------------- #

SOURCE = """Enterprise Sales Executive — Acme SaaS (2019 – 2023)
Managed a quota-carrying B2B pipeline across Turkey."""


def judgment(quotes: list[str], verdict="met") -> ReqJudgment:
    return ReqJudgment(
        req_id="S1", verdict=verdict, score=0.9, confidence=0.9,
        info_status="explicit",
        evidence=[EvidenceQuote(quote=q) for q in quotes],
        reasoning="…",
    )


def test_real_quote_survives_whitespace_and_case():
    j = judgment(["enterprise sales executive — acme saas"])
    result = verify_judgment(j, SOURCE)
    assert result.dropped_quotes == 0
    assert result.degraded is False
    assert len(result.judgment.evidence) == 1


def test_fabricated_quote_dropped_and_verdict_degraded():
    j = judgment(["10 years at Google as VP Sales"])
    result = verify_judgment(j, SOURCE)
    assert result.dropped_quotes == 1
    assert result.degraded is True
    assert result.judgment.evidence == []
    assert result.judgment.confidence <= 0.4
    assert result.judgment.info_status == "ambiguous"


def test_mixed_quotes_keep_only_verified():
    j = judgment(["quota-carrying B2B pipeline", "CEO of Microsoft"])
    result = verify_judgment(j, SOURCE)
    assert len(result.judgment.evidence) == 1
    assert result.degraded is False  # one real quote remains


def test_unknown_verdict_untouched():
    j = judgment([], verdict="unknown")
    result = verify_judgment(j, SOURCE)
    assert result.judgment == j


def test_verify_all_stats():
    verified, stats = verify_all(
        [judgment(["quota-carrying B2B pipeline"]), judgment(["fabricated text"])], SOURCE
    )
    assert stats["dropped_quotes"] == 1
    assert stats["degraded_req_ids"] == ["S1"]
    assert len(verified) == 2
