"""Pull production corrections into the corpus, where they become CI gates.

This is the flywheel closing. A recruiter correcting a verdict is the most
expensive label money can buy — a domain expert, on a real job, on a real
candidate, with the CV in front of them — and until now it only changed one
screen. Harvesting turns it into a permanent regression test.

Two rules make that safe:

- **The profile is de-identified on the way out.** Corpus cases carry no
  personal data, and the pseudonym is keyed by a salt that lives outside the
  repository (see deidentify.py). The production row is not modified.
- **Only corrected requirements become labels.** What a model said and nobody
  challenged is not ground truth; it is the thing under test. Importing it
  would teach the suite to expect today's answers forever.
"""

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from senthire.db.models import (
    Application,
    CandidateProfileRow,
    EvaluationSpecRow,
    Job,
    Override,
    ScreeningRun,
)
from senthire.domain.spec import EvaluationSpec
from senthire.evals.corpus import AutoLabel, LabelSet, Pool, make_case


@dataclass
class HarvestReport:
    imported: int = 0
    duplicates: int = 0
    labels: int = 0
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def harvest_corrections(
    session: Session,
    pool: Pool,
    *,
    source_job_id,
    job_name: str,
    salt: str,
    as_of: date | None = None,
) -> HarvestReport:
    """Import every corrected candidate of one production job into `pool`."""
    report = HarvestReport()
    job = session.get(Job, source_job_id)
    if job is None:
        raise ValueError(f"job {source_job_id} not found")

    run = session.scalars(
        select(ScreeningRun)
        .where(ScreeningRun.job_id == job.id, ScreeningRun.status == "complete")
        .order_by(ScreeningRun.started_at.desc())
        .limit(1)
    ).first()
    if run is None:
        raise ValueError("this job has no completed screening run to harvest")

    spec_row = session.get(EvaluationSpecRow, run.spec_id)
    spec = EvaluationSpec.model_validate(spec_row.spec)
    pool.save_spec(job_name, spec)

    overrides = session.scalars(
        select(Override)
        .where(
            Override.run_id == run.id,
            Override.action == "correct",
            Override.req_id.is_not(None),
        )
        .order_by(Override.created_at)
    ).all()
    if not overrides:
        return report

    label_set = pool.labels(job_name) or LabelSet(
        pool=pool.name, job=job_name, spec_version=spec.version,
        labeled_at=as_of or date.today(),
    )
    label_set.oracle = {"source": "production overrides", "job": str(job.id)}

    by_application: dict = {}
    for override in overrides:
        # Later corrections supersede earlier ones on the same requirement.
        by_application.setdefault(override.application_id, {})[override.req_id] = override

    for application_id, corrections in by_application.items():
        application = session.get(Application, application_id)
        if application is None:
            report.skip("application_missing")
            continue
        profile_row = session.scalars(
            select(CandidateProfileRow)
            .where(CandidateProfileRow.document_id == application.document_id)
            .order_by(CandidateProfileRow.version.desc())
            .limit(1)
        ).first()
        if profile_row is None:
            report.skip("no_stored_profile")
            continue

        case = make_case(
            profile_row.profile,
            salt=salt,
            seed=str(profile_row.document_id),
            text=profile_row.raw_text,
            imported_at=as_of or date.today(),
            source={
                "kind": "harvested",
                "extractor_model": profile_row.extractor_model,
                "profile_version": profile_row.version,
            },
            tags=["harvested"],
        )
        if pool.add(case):
            report.imported += 1
        else:
            report.duplicates += 1

        labels = label_set.cases.setdefault(case.corpus_id, {})
        for req_id, override in corrections.items():
            labels[req_id] = AutoLabel(
                verdict=override.to_verdict,
                confidence=1.0,
                source="human",
                agreement=1.0,
                rationale=override.reason or "corrected by a recruiter in production",
            )
            report.labels += 1

    pool.save_labels(label_set)
    return report


def correction_summary(session: Session, org_id=None) -> list[dict]:
    """How many corrections exist per job — where harvesting is worth running."""
    query = (
        select(Override.run_id, ScreeningRun.job_id, Job.title)
        .join(ScreeningRun, ScreeningRun.id == Override.run_id)
        .join(Job, Job.id == ScreeningRun.job_id)
        .where(Override.action == "correct")
    )
    if org_id is not None:
        query = query.where(Override.org_id == org_id)
    counts: dict = {}
    for _run_id, job_id, title in session.execute(query):
        entry = counts.setdefault(str(job_id), {"job_id": str(job_id), "title": title, "corrections": 0})
        entry["corrections"] += 1
    return sorted(counts.values(), key=lambda row: -row["corrections"])
