"""Self-consistency voting for borderline deep analyses (docs/07 §7).

A candidate selected for deep analysis because the pipeline is UNSURE — a
borderline knockout, an unverified hard requirement, low confidence on a
heavy requirement — gets K independent deep passes instead of one. Majority
wins per requirement; a met-vs-not_met split, a no-majority pool, or a pool
whose evidence all failed verification is flagged for human review instead
of silently averaged. Candidates selected merely for being near the top
("decision_band") keep the single pass: the extra spend goes where the
uncertainty is, which is the only place it buys anything.

Sampling: vote 1 runs at the standard judge temperature, so a voting run's
first pass is byte-for-byte the call a non-voting run makes; votes 2..K
sample at a higher temperature because self-consistency needs diverse
reasoning paths, not the same path repeated three times.

Interactive transport only — the batch path stays single-pass by design
(docs/08): it is the bulk cost-optimized lane, and tripling it would cancel
exactly the discount it exists for.
"""

from collections import Counter
from statistics import median_low

from senthire.config import get_settings
from senthire.domain.spec import EvaluationSpec
from senthire.screening.evidence import quotes_supported
from senthire.screening.llm import LlmUsage, ScreeningCallFailed, deep_analyze
from senthire.screening.schemas import DeepAnalysisOutput, ReqJudgment

# Selection reasons that mean "the pipeline is unsure", as opposed to "the
# candidate is near the top". Only unsure candidates are worth extra votes.
UNCERTAINTY_REASONS = {
    "borderline_hard_filter",
    "hard_requirement_unverified",
    "low_confidence_on_heavy_requirement",
}

# Ordered for severity, like the labeling oracle: met vs not_met in one pool
# is a different animal from met vs partially_met. "unknown" carries no rank.
VERDICT_RANK = {"not_met": 0, "partially_met": 1, "met": 2}


def vote_count(reasons: list[str] | None) -> int:
    """How many deep passes this candidate's selection reasons earn."""
    settings = get_settings()
    if settings.deep_borderline_votes > 1 and UNCERTAINTY_REASONS & set(reasons or []):
        return settings.deep_borderline_votes
    return 1


def _merge_requirement(
    pool_all: list[ReqJudgment], raw_text: str
) -> tuple[ReqJudgment, dict, bool]:
    """Fold one requirement's votes into a judgment + meta + review flag."""
    pool = [j for j in pool_all if j.verdict == "unknown" or quotes_supported(j, raw_text)]
    excluded = len(pool_all) - len(pool)
    if not pool:
        # Every vote rested on quotes the document does not contain. Keep the
        # first pass's judgment — downstream evidence verification will strip
        # and degrade it — and send a human.
        return pool_all[0], {"votes": {}, "agreement": 0.0, "excluded": excluded}, True

    counts = Counter(j.verdict for j in pool)
    verdict, top = counts.most_common(1)[0]
    agreement = top / len(pool)
    ranks = [VERDICT_RANK[v] for v in counts if v in VERDICT_RANK]
    severe_split = bool(ranks) and (max(ranks) - min(ranks)) >= 2
    meta = {"votes": dict(counts), "agreement": round(agreement, 3)}
    if excluded:
        meta["excluded"] = excluded

    if top * 2 <= len(pool):  # no majority — keep the first pass, flag it
        return pool[0], meta, True

    winners = [j for j in pool if j.verdict == verdict]
    scores = [j.score for j in winners if j.score is not None]
    chosen_score = median_low(sorted(scores)) if scores else None
    chosen = next((j for j in winners if j.score == chosen_score), winners[0])
    # Confidence is what the majority claimed, tempered by how much of the
    # pool it actually was — the oracle's honesty rule, applied in production.
    mean_confidence = sum(j.confidence for j in winners) / len(winners)
    chosen = chosen.model_copy(update={"confidence": round(mean_confidence * agreement, 3)})
    return chosen, meta, severe_split


def deep_vote(
    spec: EvaluationSpec,
    profile: dict,
    raw_text: str,
    light_judgments: list[dict],
    *,
    votes: int,
    analyze=deep_analyze,
) -> tuple[DeepAnalysisOutput, list[LlmUsage], dict]:
    """K independent deep passes folded into one output plus vote provenance.

    Narrative fields (summary, strengths, corrections) come from the first
    pass; the voting is about verdicts. A vote failing permanently after the
    first succeeded degrades the pool instead of failing the candidate.
    `analyze` is injectable so the caller's (possibly patched) binding is the
    one that runs — the transport must never fork between one vote and K."""
    settings = get_settings()
    outputs: list[DeepAnalysisOutput] = []
    usages: list[LlmUsage] = []
    errors = 0
    for index in range(votes):
        temperature = None if index == 0 else settings.deep_vote_temperature
        try:
            output, usage = analyze(
                spec, profile, raw_text, light_judgments, temperature=temperature
            )
        except ScreeningCallFailed:
            if not outputs:
                raise  # first pass failing is exactly today's single-call failure
            errors += 1
            continue
        outputs.append(output)
        if usage is not None:
            usages.append(usage)

    primary = outputs[0]
    req_order: list[str] = []
    for output in outputs:
        for judgment in output.judgments:
            if judgment.req_id not in req_order:
                req_order.append(judgment.req_id)

    merged: list[ReqJudgment] = []
    per_requirement: dict[str, dict] = {}
    flagged: list[str] = []
    for req_id in req_order:
        pool_all = [j for o in outputs for j in o.judgments if j.req_id == req_id]
        judgment, meta, needs_review = _merge_requirement(pool_all, raw_text)
        merged.append(judgment)
        per_requirement[req_id] = meta
        if needs_review:
            flagged.append(req_id)

    vote_meta = {
        "requested": votes,
        "completed": len(outputs),
        "vote_errors": errors,
        "flagged": flagged,
        "per_requirement": per_requirement,
    }
    return primary.model_copy(update={"judgments": merged}), usages, vote_meta
