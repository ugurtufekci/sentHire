"""Verdict merging + the stored explanation document (docs/06 §4).

Pure functions. The merge rules encode the stage hierarchy:
- deterministic verdicts are authoritative for what code can check;
- deep overrides light for semantic judgments;
- hybrid gates take the most severe of (deterministic, semantic) — the
  arithmetic and the judgment both have veto power on a gate;
- hybrid scored requirements prefer the semantic judgment, falling back to the
  deterministic signal when the model abstained.
"""

from senthire.domain.scoring import RequirementVerdict, ScoreResult
from senthire.domain.spec import EvaluationSpec
from senthire.screening.schemas import ReqJudgment

_SEVERITY = {"not_met": 3, "unknown": 2, "partially_met": 1, "met": 0, "disqualified": 4}


def judgment_to_verdict(judgment: ReqJudgment, stage: str) -> RequirementVerdict:
    return RequirementVerdict(
        req_id=judgment.req_id,
        verdict=judgment.verdict,
        score=judgment.score,
        confidence=judgment.confidence,
        info_status=judgment.info_status,
        evidence=[e.model_dump() for e in judgment.evidence],
        source_stage=stage,  # type: ignore[arg-type]
    )


def judgments_to_verdicts(judgments: list[ReqJudgment], stage: str) -> dict[str, RequirementVerdict]:
    return {j.req_id: judgment_to_verdict(j, stage) for j in judgments}


def merge_verdicts(
    spec: EvaluationSpec,
    det: dict[str, RequirementVerdict],
    light: dict[str, RequirementVerdict] | None = None,
    deep: dict[str, RequirementVerdict] | None = None,
) -> dict[str, RequirementVerdict]:
    semantic = dict(light or {})
    semantic.update(deep or {})  # deep overrides light per req_id

    merged: dict[str, RequirementVerdict] = {}
    for req in spec.requirements:
        det_v = det.get(req.req_id)
        sem_v = semantic.get(req.req_id)
        if req.evaluator == "deterministic":
            if det_v is not None:
                merged[req.req_id] = det_v
        elif req.evaluator == "semantic":
            if sem_v is not None:
                merged[req.req_id] = sem_v
            elif det_v is not None:  # compiler downgrades can leave stale det results
                merged[req.req_id] = det_v
        else:  # hybrid
            combined = _combine_hybrid(req.type, det_v, sem_v)
            if combined is not None:
                merged[req.req_id] = combined
    return merged


def _combine_hybrid(
    req_type: str, det_v: RequirementVerdict | None, sem_v: RequirementVerdict | None
) -> RequirementVerdict | None:
    if det_v is None:
        return sem_v
    if sem_v is None:
        return det_v
    if req_type in {"hard", "disqualifier", "penalty"}:
        # both halves have veto power on gates; keep the richer (semantic) envelope
        worst = det_v if _SEVERITY[det_v.verdict] >= _SEVERITY[sem_v.verdict] else sem_v
        return sem_v.model_copy(
            update={"verdict": worst.verdict, "score": worst.effective_score(),
                    "borderline": det_v.borderline or sem_v.borderline}
        )
    # scored / bonus / info: judgment wins unless the model abstained
    return sem_v if sem_v.verdict != "unknown" else det_v


def build_result_document(
    spec: EvaluationSpec,
    verdicts: dict[str, RequirementVerdict],
    score_result: ScoreResult,
    *,
    stage_reached: str,
    narrative: dict | None = None,
    corrections: list[dict] | None = None,
    deep_reasons: list[str] | None = None,
    evidence_stats: dict | None = None,
    models_used: dict | None = None,
) -> dict:
    """The evaluations.result JSONB — everything the results UI renders."""
    rejection_reasons = None
    if score_result.gate.status == "fail":
        rejection_reasons = []
        for req_id in score_result.gate.failed:
            req = spec.by_id(req_id)
            verdict = verdicts.get(req_id)
            rejection_reasons.append(
                {
                    "req_id": req_id,
                    "label": req.display_label(spec.locale) if req else req_id,
                    "verdict": verdict.verdict if verdict else "not_met",
                    "evidence": verdict.evidence if verdict else [],
                }
            )

    requirement_rows = []
    for req in spec.requirements:
        verdict = verdicts.get(req.req_id)
        if verdict is None:
            continue
        requirement_rows.append(
            {
                "req_id": req.req_id,
                "label": req.label,
                "category": req.category,
                "type": req.type,
                "verdict": verdict.verdict,
                "score": verdict.effective_score(),
                "confidence": verdict.effective_confidence(),
                "info_status": verdict.info_status,
                "evidence": verdict.evidence,
                "source_stage": verdict.source_stage,
                "borderline": verdict.borderline,
            }
        )

    return {
        "stage_reached": stage_reached,
        "gate": score_result.gate.model_dump(),
        "categories": {c: cs.model_dump() for c, cs in score_result.categories.items()},
        "base_score": score_result.base_score,
        "adjustments": [a.model_dump() for a in score_result.adjustments],
        "final_score": score_result.final_score,
        "band": score_result.band,
        "confidence": score_result.confidence,
        "needs_review": score_result.needs_review,
        "review_reasons": score_result.review_reasons,
        "missing_information": score_result.missing_information,
        "requirements": requirement_rows,
        "verdicts": {rid: v.model_dump() for rid, v in verdicts.items()},  # for memo/re-score
        "rejection_reasons": rejection_reasons,
        "narrative": narrative or {},
        "corrections": corrections or [],
        "deep_selection_reasons": deep_reasons or [],
        "evidence_stats": evidence_stats or {},
        "models_used": models_used or {},
        "scorer_version": score_result.scorer_version,
    }


def verdicts_from_result_document(result: dict) -> dict[str, RequirementVerdict]:
    return {
        rid: RequirementVerdict.model_validate(v) for rid, v in (result.get("verdicts") or {}).items()
    }
