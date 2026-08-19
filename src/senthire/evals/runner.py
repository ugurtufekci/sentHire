"""Run golden cases through the real pipeline stages and diff against labels.

Offline (default): deterministic predicates + merge + scorer run for real;
hand-labeled semantic verdicts stand in for model output. Every mismatch is a
regression in deterministic code, the spec, or the labels — the run must be
100% clean.

Live (--live): additionally calls the real light-screening model per candidate
and grades its semantic verdicts against the labels (the answer key). Model
quality drifts, so live mode reports an agreement rate against a threshold
instead of demanding exactness.
"""

from dataclasses import dataclass, field

from senthire.domain.scoring import RequirementVerdict, ScoreResult, score
from senthire.evals.document import profile_document
from senthire.evals.loader import GoldenCase
from senthire.evals.schema import GoldenCandidate
from senthire.screening.assemble import judgments_to_verdicts, merge_verdicts
from senthire.screening.deterministic import run_deterministic_stage


@dataclass
class CandidateOutcome:
    golden_id: str
    gate: str  # pass | fail
    borderline: bool
    knockout_reqs: list[str]
    result: ScoreResult
    mismatches: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches


@dataclass
class CaseReport:
    name: str
    outcomes: list[CandidateOutcome]
    case_mismatches: list[str] = field(default_factory=list)
    ranking: list[str] = field(default_factory=list)  # gate-pass golden_ids, best first

    @property
    def mismatch_count(self) -> int:
        return len(self.case_mismatches) + sum(len(o.mismatches) for o in self.outcomes)


def _labeled_semantic_verdicts(cand: GoldenCandidate) -> dict[str, RequirementVerdict]:
    return {
        req_id: RequirementVerdict(
            req_id=req_id,
            verdict=label.verdict,
            score=label.score,
            confidence=label.confidence,
            info_status=label.info_status,
            source_stage="light",
        )
        for req_id, label in cand.labels.semantic.items()
    }


@dataclass
class PipelineOutcome:
    """What the deterministic half of the pipeline says about one profile."""

    gate: str
    knockouts: list[str]
    borderline: bool
    result: ScoreResult
    deterministic: dict[str, RequirementVerdict]
    merged: dict[str, RequirementVerdict]


def evaluate_profile(
    spec, profile: dict, semantic: dict[str, RequirementVerdict], as_of
) -> PipelineOutcome:
    """Run Stages 3 and 6 for real over a profile and a set of semantic verdicts.

    Shared by the golden runner and the corpus promoter so a promoted
    expectation and the assertion that later checks it are computed by exactly
    the same code path.
    """
    det = run_deterministic_stage(spec, profile_document(profile, as_of))
    merged = merge_verdicts(spec, det.verdicts, light=semantic)
    result = score(spec, merged)
    return PipelineOutcome(
        gate="fail" if det.knocked_out or result.gate.status == "fail" else "pass",
        knockouts=sorted(set(det.knockout_reasons) | set(result.gate.failed)),
        borderline=det.borderline,
        result=result,
        deterministic=det.verdicts,
        merged=merged,
    )


def evaluate_candidate(case: GoldenCase, cand: GoldenCandidate) -> CandidateOutcome:
    outcome_data = evaluate_profile(
        case.spec, cand.profile, _labeled_semantic_verdicts(cand), case.expectations.as_of
    )
    det_verdicts, merged, result = (
        outcome_data.deterministic, outcome_data.merged, outcome_data.result
    )
    outcome = CandidateOutcome(
        golden_id=cand.golden_id,
        gate=outcome_data.gate,
        borderline=outcome_data.borderline,
        knockout_reqs=outcome_data.knockouts,
        result=result,
    )

    labels = cand.labels
    m = outcome.mismatches
    for req_id, expected in labels.expected_deterministic.items():
        got = det_verdicts.get(req_id)
        if got is None or got.verdict != expected:
            m.append(
                f"{req_id} (deterministic): expected {expected}, "
                f"got {got.verdict if got else 'absent'}"
            )
    for req_id, expected in labels.expected_merged.items():
        got = merged.get(req_id)
        if got is None or got.verdict != expected:
            m.append(
                f"{req_id} (merged): expected {expected}, "
                f"got {got.verdict if got else 'absent'}"
            )
    if outcome.gate != labels.gate:
        m.append(f"gate: expected {labels.gate}, got {outcome.gate}")
    if (
        labels.gate == "fail"
        and labels.knockout_reqs
        and set(labels.knockout_reqs) != set(outcome.knockout_reqs)
    ):
        m.append(
            f"knockout_reqs: expected {sorted(labels.knockout_reqs)}, "
            f"got {outcome.knockout_reqs}"
        )
    if outcome.borderline != labels.borderline:
        m.append(f"borderline: expected {labels.borderline}, got {outcome.borderline}")
    if labels.band is not None and result.band != labels.band:
        m.append(f"band: expected {labels.band}, got {result.band}")
    if labels.score_range is not None:
        lo, hi = labels.score_range
        if not (lo <= result.final_score <= hi):
            m.append(f"score: expected within [{lo}, {hi}], got {result.final_score}")
    if labels.needs_review is not None and result.needs_review != labels.needs_review:
        m.append(f"needs_review: expected {labels.needs_review}, got {result.needs_review}")
    return outcome


def run_case(case: GoldenCase) -> CaseReport:
    outcomes = [evaluate_candidate(case, c) for c in case.candidates]
    by_id = {o.golden_id: o for o in outcomes}

    # Deterministic ranking: score desc, golden_id as the stable tie-break.
    ranking = [
        o.golden_id
        for o in sorted(
            (o for o in outcomes if o.gate == "pass"),
            key=lambda o: (-o.result.final_score, o.golden_id),
        )
    ]
    report = CaseReport(case.name, outcomes, ranking=ranking)
    m = report.case_mismatches

    exp = case.expectations
    if exp.expected_top:
        top = set(ranking[: exp.top_k])
        missing = [g for g in exp.expected_top if g not in top]
        if missing:
            m.append(
                f"ranking: {missing} expected in top-{exp.top_k}, got {ranking[: exp.top_k]}"
            )
    for higher, lower in exp.expected_order_pairs:
        if higher in ranking and lower in ranking:
            if ranking.index(higher) > ranking.index(lower):
                m.append(f"order: expected {higher} above {lower}")
        else:
            m.append(f"order: {higher} vs {lower} — one of them did not pass the gate")
    for higher, lower in exp.monotonic_pairs:
        # Both sides are scored even when gated out: an invariant about the
        # score must not be silently skipped because the gate happened first.
        hi, lo = by_id[higher].result, by_id[lower].result
        if hi.final_score < lo.final_score:
            m.append(
                f"monotonicity: {higher} ({hi.final_score}) scored below "
                f"{lower} ({lo.final_score})"
            )
    for a, b in exp.fairness_pairs:
        ra, rb = by_id[a].result, by_id[b].result
        if ra.final_score != rb.final_score or ra.band != rb.band:
            m.append(
                f"fairness: {a} ({ra.final_score}/{ra.band}) != {b} ({rb.final_score}/{rb.band})"
            )
    return report


# --- live mode -------------------------------------------------------------


@dataclass
class LiveReqOutcome:
    golden_id: str
    req_id: str
    expected: str
    got: str
    exact: bool
    adjacent: bool  # met <-> partially_met near-misses


@dataclass
class LiveCaseReport:
    name: str
    rows: list[LiveReqOutcome] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def agreement(self) -> float | None:
        return (sum(r.exact for r in self.rows) / len(self.rows)) if self.rows else None


_ADJACENT = {frozenset({"met", "partially_met"}), frozenset({"partially_met", "not_met"})}


def run_case_live(case: GoldenCase) -> LiveCaseReport:
    """Grade the real light-screening model against the labeled answer key."""
    from senthire.screening.llm import light_screen  # imported lazily: needs API key

    report = LiveCaseReport(case.name)
    for cand in case.candidates:
        profile_doc = profile_document(cand.profile, case.expectations.as_of)
        try:
            output, usage = light_screen(case.spec, profile_doc)
        except Exception as exc:  # transient/API errors fail the row, not the run
            report.errors.append(f"{cand.golden_id}: {type(exc).__name__}: {exc}")
            continue
        report.input_tokens += usage.input_tokens
        report.output_tokens += usage.output_tokens
        report.cache_read_tokens += usage.cache_read_tokens

        got = judgments_to_verdicts(output.judgments, "light")
        for req_id, label in cand.labels.semantic.items():
            got_verdict = got[req_id].verdict if req_id in got else "absent"
            report.rows.append(
                LiveReqOutcome(
                    golden_id=cand.golden_id,
                    req_id=req_id,
                    expected=label.verdict,
                    got=got_verdict,
                    exact=got_verdict == label.verdict,
                    adjacent=frozenset({got_verdict, label.verdict}) in _ADJACENT,
                )
            )
    return report
