"""What the workspace learned from its own decisions.

Two signals, both produced by people using the product rather than by any
model:

- **Corrections** say the screening was wrong in a particular, repeated way.
  One override is an exception; the same requirement corrected in a third of
  candidates is a mis-specified criterion, and that is worth saying out loud.
- **Outcomes** say what the scores were worth. The pipeline records who was
  actually contacted, interviewed and hired, so the score at which this
  workspace really starts taking people seriously is measurable instead of
  assumed.

Everything here is deliberately conservative about small samples. A threshold
derived from four candidates is noise wearing a number, and presenting it as
insight would be the exact failure mode this product exists to avoid — so the
sample size travels with every figure and weak evidence stays silent.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from senthire.db.models import Application, Evaluation, Job, Override, ScreeningRun
from senthire.domain.spec import EvaluationSpec

# Below this many candidates a rate is an anecdote, not a rate.
MIN_SAMPLE_FOR_RATE = 8
# A requirement corrected this often is telling you something about the spec.
NOTABLE_CORRECTION_RATE = 0.2
# Stages that mean a human decided to spend time on this candidate.
ADVANCED_STAGES = {"contacted", "interviewing", "offer", "hired"}
SCORE_BUCKETS = [(90, None), (80, 90), (70, 80), (60, 70), (None, 60)]


@dataclass
class Insight:
    kind: str
    severity: str  # info | notable
    message_tr: str
    detail: dict


def _latest_run(session: Session, job: Job) -> ScreeningRun | None:
    return session.scalars(
        select(ScreeningRun)
        .where(ScreeningRun.job_id == job.id, ScreeningRun.status == "complete")
        .order_by(ScreeningRun.started_at.desc())
        .limit(1)
    ).first()


def correction_patterns(session: Session, job: Job, spec: EvaluationSpec | None) -> dict:
    """Per requirement: how often a human disagreed, and in which direction."""
    run = _latest_run(session, job)
    if run is None:
        return {"sample_size": 0, "requirements": []}

    evaluated = session.scalars(
        select(Evaluation.application_id).where(Evaluation.run_id == run.id)
    ).all()
    sample = len(evaluated)
    overrides = session.scalars(
        select(Override).where(
            Override.run_id == run.id, Override.action == "correct", Override.req_id.is_not(None)
        )
    ).all()

    per_req: dict[str, dict] = {}
    for row in overrides:
        bucket = per_req.setdefault(
            row.req_id, {"req_id": row.req_id, "applications": set(), "directions": {}}
        )
        bucket["applications"].add(row.application_id)
        key = f"{row.from_verdict or 'unknown'}→{row.to_verdict}"
        bucket["directions"][key] = bucket["directions"].get(key, 0) + 1

    rows = []
    for req_id, bucket in per_req.items():
        corrected = len(bucket["applications"])
        requirement = spec.by_id(req_id) if spec else None
        rows.append(
            {
                "req_id": req_id,
                "label": requirement.display_label("tr") if requirement else req_id,
                "corrected": corrected,
                "rate": round(corrected / sample, 3) if sample else None,
                "directions": bucket["directions"],
            }
        )
    rows.sort(key=lambda r: -r["corrected"])
    return {"sample_size": sample, "requirements": rows}


def outcome_calibration(session: Session, job: Job) -> dict:
    """Score against what people actually did with the candidate."""
    applications = session.scalars(
        select(Application).where(Application.job_id == job.id)
    ).all()
    if not applications:
        return {"sample_size": 0, "buckets": [], "advanced": 0}

    scores = {
        evaluation.application_id: evaluation.overall_score
        for evaluation in session.scalars(
            select(Evaluation)
            .where(Evaluation.application_id.in_([a.id for a in applications]))
            .order_by(Evaluation.created_at)
        )
    }

    scored = [
        (scores[a.id], a.stage)
        for a in applications
        if scores.get(a.id) is not None
    ]
    buckets = []
    for low, high in SCORE_BUCKETS:
        members = [
            (score, stage)
            for score, stage in scored
            if (low is None or score >= low) and (high is None or score < high)
        ]
        if not members:
            continue
        advanced = sum(1 for _, stage in members if stage in ADVANCED_STAGES)
        buckets.append(
            {
                "from": low,
                "to": high,
                "count": len(members),
                "advanced": advanced,
                "hired": sum(1 for _, stage in members if stage == "hired"),
                "dropped": sum(1 for _, stage in members if stage == "dropped"),
                "advance_rate": round(advanced / len(members), 3),
            }
        )

    advanced_scores = sorted(score for score, stage in scored if stage in ADVANCED_STAGES)
    working_threshold = None
    if len(advanced_scores) >= MIN_SAMPLE_FOR_RATE:
        # The 20th percentile of the people you actually pursued: four in five
        # of them scored at or above this.
        index = max(0, int(len(advanced_scores) * 0.2) - 1)
        working_threshold = round(advanced_scores[index], 1)

    return {
        "sample_size": len(scored),
        "advanced": len(advanced_scores),
        "buckets": buckets,
        "working_threshold": working_threshold,
    }


def summarize(corrections: dict, calibration: dict) -> list[dict]:
    """The two or three sentences worth putting on a screen."""
    out: list[Insight] = []
    sample = corrections["sample_size"]
    if sample >= MIN_SAMPLE_FOR_RATE:
        for row in corrections["requirements"]:
            if (row["rate"] or 0) < NOTABLE_CORRECTION_RATE:
                continue
            direction = max(row["directions"].items(), key=lambda kv: kv[1])[0]
            out.append(
                Insight(
                    kind="correction_rate",
                    severity="notable",
                    message_tr=(
                        f"“{row['label']}” kriterini adayların %{round(row['rate'] * 100)}"
                        f"'inde düzelttiniz ({direction}). Kriter fazla dar olabilir — "
                        "ilanı yeniden derlerken bu cümleyi gözden geçirin."
                    ),
                    detail=row,
                )
            )

    threshold = calibration.get("working_threshold")
    if threshold is not None:
        out.append(
            Insight(
                kind="working_threshold",
                severity="info",
                message_tr=(
                    f"Görüşmeye aldığınız adayların beşte dördü {threshold} puan ve "
                    "üzerinde. Kısa liste eşiğiniz pratikte burası."
                ),
                detail={"threshold": threshold, "advanced": calibration["advanced"]},
            )
        )

    strong_ignored = next(
        (b for b in calibration.get("buckets", []) if b["from"] == 80 and b["advance_rate"] == 0),
        None,
    )
    if strong_ignored and strong_ignored["count"] >= 3:
        out.append(
            Insight(
                kind="unworked_top",
                severity="notable",
                message_tr=(
                    f"80+ puan alan {strong_ignored['count']} adayla henüz temas "
                    "kurulmamış. Aday akışında bekliyorlar."
                ),
                detail=strong_ignored,
            )
        )
    return [insight.__dict__ for insight in out]
