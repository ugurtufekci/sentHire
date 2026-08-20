"""Human corrections to a verdict, and what they do to the score.

The product line is "LLMs judge, code decides". An override is the third judge:
a person disagreeing with a verdict. It does not edit the score directly —
that would put a thumb on the scale invisibly. It replaces one *verdict* and
lets the same deterministic scorer run again, so the resulting number is
explainable in exactly the way every other number is.

Two consequences follow, both intended:
- a corrected hard requirement can lift a candidate back through the gate;
- the run is re-ranked, because a changed score that left the order alone
  would be a lie.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from senthire.db.models import Evaluation, Override, User
from senthire.domain.ranking import rank_key
from senthire.domain.scoring import RequirementVerdict, score
from senthire.domain.spec import EvaluationSpec
from senthire.screening.assemble import build_result_document, verdicts_from_result_document

CORRECTABLE_VERDICTS = {"met", "partially_met", "not_met", "unknown"}


class OverrideError(ValueError):
    pass


def correct_verdict(
    session: Session,
    *,
    evaluation: Evaluation,
    spec: EvaluationSpec,
    req_id: str,
    verdict: str,
    reason: str | None,
    user: User,
) -> Evaluation:
    """Record the correction, re-score the candidate, re-rank the run."""
    if verdict not in CORRECTABLE_VERDICTS:
        raise OverrideError(f"verdict must be one of {sorted(CORRECTABLE_VERDICTS)}")
    if spec.by_id(req_id) is None:
        raise OverrideError(f"'{req_id}' is not a requirement of this job")

    verdicts = verdicts_from_result_document(evaluation.result or {})
    previous = verdicts.get(req_id)
    session.add(
        Override(
            org_id=evaluation.org_id,
            application_id=evaluation.application_id,
            run_id=evaluation.run_id,
            user_id=user.id,
            action="correct",
            req_id=req_id,
            from_verdict=previous.verdict if previous else None,
            to_verdict=verdict,
            reason=reason,
        )
    )
    # A person looked at the CV: confidence is not in question, and the
    # evidence they were looking at (the model's) is kept for the audit trail.
    verdicts[req_id] = RequirementVerdict(
        req_id=req_id,
        verdict=verdict,
        confidence=1.0,
        info_status=previous.info_status if previous else None,
        evidence=previous.evidence if previous else [],
        source_stage="human",
    )
    _rescore(session, evaluation, spec, verdicts)
    rerank_run(session, evaluation.run_id)
    return evaluation


def _rescore(
    session: Session,
    evaluation: Evaluation,
    spec: EvaluationSpec,
    verdicts: dict[str, RequirementVerdict],
) -> None:
    previous = evaluation.result or {}
    result_score = score(spec, verdicts)
    result = build_result_document(
        spec,
        verdicts,
        result_score,
        stage_reached=previous.get("stage_reached", evaluation.stage_reached),
        narrative=previous.get("narrative"),
        corrections=previous.get("corrections"),
        deep_reasons=previous.get("deep_selection_reasons"),
        evidence_stats=previous.get("evidence_stats"),
        models_used=previous.get("models_used"),
    )
    result["human_overrides"] = [
        {
            "req_id": row.req_id,
            "from": row.from_verdict,
            "to": row.to_verdict,
            "reason": row.reason,
            "at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in history(session, evaluation.application_id, run_id=evaluation.run_id)
    ]
    evaluation.result = result
    evaluation.overall_score = result_score.final_score
    evaluation.band = result_score.band
    evaluation.confidence = result_score.confidence
    evaluation.hard_result = result_score.gate.status


def rerank_run(session: Session, run_id: uuid.UUID) -> None:
    evaluations = session.scalars(
        select(Evaluation).where(Evaluation.run_id == run_id)
    ).all()
    passed = [e for e in evaluations if e.hard_result == "pass"]
    passed.sort(key=lambda e: rank_key(e.overall_score, e.confidence, e.application_id))
    for position, evaluation in enumerate(passed, start=1):
        evaluation.rank = position
    for evaluation in evaluations:
        if evaluation.hard_result != "pass":
            evaluation.rank = None
            evaluation.band = "rejected"


def history(
    session: Session, application_id: uuid.UUID, *, run_id: uuid.UUID | None = None
) -> list[Override]:
    """Corrections for one application, newest first.

    Includes the row just added in this transaction: the caller flushes before
    reading so the rebuilt result document lists the correction that caused it.
    """
    session.flush()
    query = select(Override).where(
        Override.application_id == application_id, Override.action == "correct"
    )
    if run_id is not None:
        query = query.where(Override.run_id == run_id)
    # now() is the transaction timestamp, so several corrections saved together
    # share it; the id tie-break keeps the order stable rather than arbitrary.
    return list(session.scalars(query.order_by(Override.created_at.desc(), Override.id)))
