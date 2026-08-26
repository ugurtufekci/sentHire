"""Stage 5 selection policy (docs/02 Stage 5) — spend deep-model money only
where it can change the outcome. Pure function over preliminary results."""

from dataclasses import dataclass, field

from senthire.domain.scoring import RequirementVerdict, ScoreResult
from senthire.domain.spec import EvaluationSpec


@dataclass
class Preliminary:
    application_id: str
    score_result: ScoreResult
    verdicts: dict[str, RequirementVerdict]
    borderline: bool = False
    reasons: list[str] = field(default_factory=list)  # filled by select_for_deep


def _requirement_weight_share(spec: EvaluationSpec, req_id: str) -> float:
    req = spec.by_id(req_id)
    if req is None or req.type not in {"scored", "hard"}:
        return 0.0
    if req.type == "hard":
        return 1.0  # gates always matter
    category_weight = spec.weights.get(req.category, 0.0)
    siblings = [r for r in spec.requirements if r.category == req.category and r.type == "scored"]
    total = sum(r.weight_within_category for r in siblings) or 1.0
    return category_weight * (req.weight_within_category / total)


def select_for_deep(
    spec: EvaluationSpec,
    prelims: list[Preliminary],
    *,
    top_k: int,
    band_extra: int,
    confidence_threshold: float,
    weight_threshold: float,
    cap: int | None = None,
) -> list[Preliminary]:
    """Returns the subset needing deep analysis, with per-candidate reasons.

    `cap` is the hard ceiling on how many candidates enter Stage 5. Without
    one, a run where the light model hedges everywhere sends the WHOLE cohort
    deep — the 220-CV rehearsal did exactly that — and borderline voting then
    multiplies it. The decision band is always fully included (it is the part
    that decides the shortlist); uncertainty picks fill the remainder in
    severity order: a possibly-wrong knockout outranks an unverified gate,
    which outranks a hedged heavy requirement. Candidates already selected
    still accumulate every reason that applies to them — the cap gates
    admissions, never explanations."""
    passed = [p for p in prelims if p.score_result.gate.status == "pass"]
    passed.sort(key=lambda p: p.score_result.final_score, reverse=True)
    band_cutoff = top_k + band_extra

    selected: dict[str, Preliminary] = {}

    def pick(p: Preliminary, reason: str, *, admit: bool = True) -> bool:
        entry = selected.get(p.application_id)
        if entry is None:
            if not admit:
                return False
            entry = selected.setdefault(p.application_id, p)
        if reason not in entry.reasons:
            entry.reasons.append(reason)
        return True

    for rank, p in enumerate(passed, start=1):
        if rank <= band_cutoff:
            pick(p, "decision_band")

    def uncertainty(p: Preliminary):
        if p.borderline:
            yield 0, "borderline_hard_filter"
        if p.score_result.gate.status == "pass" and p.score_result.gate.unverified:
            yield 1, "hard_requirement_unverified"
        for req_id, verdict in p.verdicts.items():
            if verdict.source_stage != "light":
                continue
            weight = _requirement_weight_share(spec, req_id)
            if weight >= weight_threshold and verdict.effective_confidence() < confidence_threshold:
                yield 2, "low_confidence_on_heavy_requirement"
                break

    flagged = []
    for p in prelims:
        for severity, reason in uncertainty(p):
            flagged.append((severity, -p.score_result.final_score, p.application_id, p, reason))
    flagged.sort(key=lambda item: item[:3])

    for _severity, _neg_score, _app_id, p, reason in flagged:
        room = cap is None or len(selected) < cap or p.application_id in selected
        pick(p, reason, admit=room)

    return list(selected.values())
