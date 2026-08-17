"""Deterministic scoring engine (docs/06) — a pure function of (spec, verdicts).

No LLM anywhere in this module. Same inputs ⇒ same score, unit-tested against
the worked example in docs/06 §3. LLM stages only ever supply the verdicts.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from senthire.domain.spec import EvaluationSpec, Requirement

SCORER_VERSION = "s1"

VerdictKind = Literal["met", "partially_met", "not_met", "unknown", "disqualified"]

_DEFAULT_SCORE: dict[str, float | None] = {
    "met": 1.0,
    "partially_met": 0.5,
    "not_met": 0.0,
    "unknown": None,
    "disqualified": 0.0,
}
_DEFAULT_CONFIDENCE = 0.7
_DEFAULT_PENALTY_POINTS = 5.0
_DEFAULT_BONUS_POINTS = 5.0

BANDS = (("top", 80.0), ("strong", 65.0), ("possible", 50.0))


class RequirementVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    req_id: str
    verdict: VerdictKind
    score: float | None = None  # 0..1 graded satisfaction; defaults from verdict
    confidence: float | None = None
    info_status: Literal["explicit", "inferred", "ambiguous", "missing"] | None = None
    evidence: list[dict] = []
    source_stage: Literal["deterministic", "light", "deep"] | None = None
    borderline: bool = False

    def effective_score(self) -> float | None:
        return self.score if self.score is not None else _DEFAULT_SCORE[self.verdict]

    def effective_confidence(self) -> float:
        return self.confidence if self.confidence is not None else _DEFAULT_CONFIDENCE


class GateResult(BaseModel):
    status: Literal["pass", "fail"]
    failed: list[str] = []  # req_ids
    unverified: list[str] = []  # hard reqs that passed only as unknown/inferred


class Adjustment(BaseModel):
    kind: Literal["bonus", "penalty"]
    req_id: str
    points: float


class CategoryScore(BaseModel):
    score: float
    weight: float
    requirements: list[str]


class ScoreResult(BaseModel):
    scorer_version: str = SCORER_VERSION
    gate: GateResult
    categories: dict[str, CategoryScore]
    base_score: float
    adjustments: list[Adjustment]
    final_score: float
    band: Literal["top", "strong", "possible", "weak"]
    confidence: float | None
    needs_review: bool
    review_reasons: list[str]
    missing_information: list[str]


def score(spec: EvaluationSpec, verdicts: dict[str, RequirementVerdict]) -> ScoreResult:
    gate = _gate(spec, verdicts)
    categories = _category_scores(spec, verdicts)
    base = _base_score(spec, categories)
    adjustments = _adjustments(spec, verdicts)

    final = base
    for adj in adjustments:
        final += adj.points if adj.kind == "bonus" else -adj.points
    final = max(0.0, min(100.0, round(final, 1)))

    confidences = [
        verdicts[r.req_id].effective_confidence()
        for r in spec.requirements
        if r.type in {"hard", "scored"} and r.req_id in verdicts
    ]
    run_confidence = round(sum(confidences) / len(confidences), 2) if confidences else None

    review_reasons: list[str] = []
    if run_confidence is not None and run_confidence < 0.6:
        review_reasons.append("low_confidence")
    if gate.unverified:
        review_reasons.append("hard_requirement_unverified")
    if any(v.verdict == "disqualified" for v in verdicts.values()):
        review_reasons.append("disqualifier_triggered")

    missing = [
        _label(spec, req_id)
        for req_id, v in verdicts.items()
        if v.verdict == "unknown" and spec.by_id(req_id) is not None
    ]

    band = next((name for name, floor in BANDS if final >= floor), "weak")

    return ScoreResult(
        gate=gate,
        categories=categories,
        base_score=round(base, 1),
        adjustments=adjustments,
        final_score=final,
        band=band,  # type: ignore[arg-type]
        confidence=run_confidence,
        needs_review=bool(review_reasons),
        review_reasons=review_reasons,
        missing_information=missing,
    )


def _gate(spec: EvaluationSpec, verdicts: dict[str, RequirementVerdict]) -> GateResult:
    failed: list[str] = []
    unverified: list[str] = []
    for req in spec.requirements:
        if req.type == "disqualifier" and verdicts.get(req.req_id, _absent(req)).verdict in {
            "met",
            "disqualified",
        }:
            failed.append(req.req_id)
        if req.type != "hard":
            continue
        v = verdicts.get(req.req_id, _absent(req))
        if v.verdict == "not_met":
            failed.append(req.req_id)
        elif v.verdict == "unknown":
            if req.missing_policy == "fail":
                failed.append(req.req_id)
            else:
                unverified.append(req.req_id)  # missing ≠ failing (docs/06 §2)
        elif v.info_status == "inferred":
            unverified.append(req.req_id)
    return GateResult(status="fail" if failed else "pass", failed=failed, unverified=unverified)


def _absent(req: Requirement) -> RequirementVerdict:
    return RequirementVerdict(req_id=req.req_id, verdict="unknown")


def _category_scores(
    spec: EvaluationSpec, verdicts: dict[str, RequirementVerdict]
) -> dict[str, CategoryScore]:
    buckets: dict[str, list[tuple[Requirement, float]]] = {}
    for req in spec.requirements:
        if req.type != "scored":
            continue
        v = verdicts.get(req.req_id)
        if v is None:
            continue
        raw = v.effective_score()
        if raw is None:
            continue  # unknown → excluded; weight redistributes via renormalization
        adjusted = raw * (0.5 + 0.5 * v.effective_confidence())
        # Deterministic verdicts are certain by construction — no confidence damping.
        if v.source_stage == "deterministic":
            adjusted = raw
        buckets.setdefault(req.category, []).append((req, adjusted))

    out: dict[str, CategoryScore] = {}
    for category, entries in buckets.items():
        weight_sum = sum(r.weight_within_category for r, _ in entries)
        if weight_sum <= 0:
            continue
        value = sum(r.weight_within_category * s for r, s in entries) / weight_sum
        out[category] = CategoryScore(
            score=round(value, 4),
            weight=spec.weights.get(category, 0.0),
            requirements=[r.req_id for r, _ in entries],
        )
    return out


def _base_score(spec: EvaluationSpec, categories: dict[str, CategoryScore]) -> float:
    active = {c: cs for c, cs in categories.items() if cs.weight > 0}
    total_weight = sum(cs.weight for cs in active.values())
    if total_weight <= 0:
        return 0.0
    return 100.0 * sum(cs.weight * cs.score for cs in active.values()) / total_weight


def _adjustments(
    spec: EvaluationSpec, verdicts: dict[str, RequirementVerdict]
) -> list[Adjustment]:
    out: list[Adjustment] = []
    bonus_total = 0.0
    for req in spec.requirements:
        v = verdicts.get(req.req_id)
        if v is None:
            continue
        if req.type == "penalty" and v.verdict == "met":
            points = (
                req.deterministic.penalty_points
                if req.deterministic and req.deterministic.penalty_points is not None
                else _DEFAULT_PENALTY_POINTS
            )
            out.append(Adjustment(kind="penalty", req_id=req.req_id, points=points))
        elif req.type == "bonus" and v.verdict in {"met", "partially_met"}:
            points = req.bonus_points if req.bonus_points is not None else _DEFAULT_BONUS_POINTS
            if v.verdict == "partially_met":
                points /= 2
            remaining = max(0.0, spec.bonus_cap - bonus_total)
            points = min(points, remaining)
            if points > 0:
                bonus_total += points
                out.append(Adjustment(kind="bonus", req_id=req.req_id, points=points))
    return out


def _label(spec: EvaluationSpec, req_id: str) -> str:
    req = spec.by_id(req_id)
    return req.display_label() if req else req_id
