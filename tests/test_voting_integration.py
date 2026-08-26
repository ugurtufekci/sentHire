"""The borderline-voting wiring, proven through the real task path: a
candidate whose selection reasons say "unsure" must get three audited deep
passes and a review flag on disagreement, while a merely-top-ranked
candidate keeps the single pass. Unit tests prove the merge; this proves
the plumbing around it — deep_application → vote_count → deep_vote →
_persist_deep_evaluation → audit ledger, against a real database.

Needs Postgres; skipped when SENTHIRE_TEST_DATABASE_URL is unset.
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, select, text

ADMIN_URL = os.environ.get("SENTHIRE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not ADMIN_URL, reason="set SENTHIRE_TEST_DATABASE_URL to run voting integration tests"
)

RAW_TEXT = (
    "Deneyim: Python ile bes yil arka uc gelistirme. "
    "Satis ekibiyle birlikte CRM entegrasyonlari kurdu."
)


@pytest.fixture(scope="module")
def db_url():
    name = f"senthire_votes_{uuid.uuid4().hex[:12]}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = ADMIN_URL.rsplit("/", 1)[0] + "/" + name

    os.environ["SENTHIRE_DATABASE_URL"] = url
    from senthire.config import get_settings

    get_settings.cache_clear()
    from senthire.db.session import get_engine, get_sessionmaker

    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    yield url

    get_engine().dispose()
    with admin.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :n AND pid <> pg_backend_pid()"
            ),
            {"n": name},
        )
        conn.execute(text(f'DROP DATABASE "{name}"'))
    admin.dispose()


def _spec():
    from senthire.domain.spec import EvaluationSpec, Requirement, SemanticCheck

    return EvaluationSpec(
        requirements=[
            Requirement(
                req_id="R1", category="relevant_experience", type="scored",
                evaluator="semantic", semantic=SemanticCheck(rubric="backend depth"),
            ),
            Requirement(
                req_id="R2", category="skills", type="scored",
                evaluator="semantic", semantic=SemanticCheck(rubric="crm integration"),
            ),
        ]
    )


def _judgment(req_id, verdict, score, quote="Python ile bes yil arka uc gelistirme"):
    from senthire.screening.schemas import EvidenceQuote, ReqJudgment

    return ReqJudgment(
        req_id=req_id,
        verdict=verdict,
        score=score,
        confidence=0.9,
        info_status="explicit",
        evidence=[EvidenceQuote(quote=quote)],
        reasoning="integration vote",
    )


def _seed_run(reasons: list[str]):
    """A run parked in deep_analysis with one light-stage evaluation."""
    from senthire.db.models import (
        Application,
        Candidate,
        CandidateProfileRow,
        Document,
        Evaluation,
        EvaluationSpecRow,
        Job,
        Organization,
        ScreeningRun,
    )
    from senthire.db.session import get_sessionmaker
    from senthire.domain.scoring import score as run_scorer
    from senthire.screening.assemble import (
        build_result_document,
        judgments_to_verdicts,
        merge_verdicts,
    )
    from senthire.screening.deterministic import run_deterministic_stage

    spec = _spec()
    profile = {"derived": {"total_experience_months": 48}}
    session = get_sessionmaker()()
    org = Organization(name="Vote Wiring A.S.")
    session.add(org)
    session.flush()
    job = Job(org_id=org.id, title="Backend", status="active")
    session.add(job)
    session.flush()
    spec_row = EvaluationSpecRow(
        org_id=org.id, job_id=job.id, version=1, status="confirmed", spec=spec.model_dump()
    )
    session.add(spec_row)
    session.flush()

    candidate = Candidate(org_id=org.id, display_name="Vote Candidate")
    session.add(candidate)
    session.flush()
    document = Document(
        org_id=org.id, candidate_id=candidate.id, upload_job_id=job.id,
        sha256=uuid.uuid4().hex, s3_key="cv/vote", mime="application/pdf",
        parse_status="parsed",
    )
    session.add(document)
    session.flush()
    application = Application(
        org_id=org.id, job_id=job.id, candidate_id=candidate.id,
        document_id=document.id, status="screened",
    )
    session.add(application)
    session.flush()
    session.add(
        CandidateProfileRow(
            org_id=org.id, document_id=document.id, candidate_id=candidate.id,
            version=1, profile=profile, raw_text=RAW_TEXT,
        )
    )

    det = run_deterministic_stage(spec, profile)
    light = judgments_to_verdicts(
        [_judgment("R1", "met", 1.0), _judgment("R2", "partially_met", 0.5)], "light"
    )
    verdicts = merge_verdicts(spec, det.verdicts, light)
    sr = run_scorer(spec, verdicts)
    doc = build_result_document(spec, verdicts, sr, stage_reached="light")

    run = ScreeningRun(
        org_id=org.id, job_id=job.id, spec_id=spec_row.id,
        mode="interactive", status="deep_analysis",
        funnel={
            "deep_reasons": {str(application.id): reasons},
            # A second, never-finished id keeps the phase from advancing, so
            # the task cannot reach a broker in this test.
            "deep_pending": [str(application.id), str(uuid.uuid4())],
        },
    )
    session.add(run)
    session.flush()
    session.add(
        Evaluation(
            org_id=org.id, run_id=run.id, application_id=application.id,
            profile_version=1, spec_version=1, pipeline_version="p1",
            stage_reached="light", hard_result="pass",
            overall_score=sr.final_score, band=sr.band, confidence=sr.confidence,
            result=doc,
        )
    )
    session.commit()
    run_id, app_id, org_id = run.id, application.id, org.id
    session.close()
    return run_id, app_id, org_id


def _scripted_deep(votes):
    from senthire.screening.llm import LlmUsage

    temperatures = []

    def fake_deep(spec, profile, raw_text, light_judgments, *, temperature=None):
        temperatures.append(temperature)
        from senthire.screening.schemas import DeepAnalysisOutput

        return (
            DeepAnalysisOutput(judgments=votes[len(temperatures) - 1], summary="derin ozet"),
            LlmUsage("fake-deep", 200, 60, 0, 0),
        )

    return fake_deep, temperatures


def _reload(run_id, app_id):
    from senthire.db.models import AuditLog, Evaluation
    from senthire.db.session import get_sessionmaker

    session = get_sessionmaker()()
    try:
        ev = session.scalar(select(Evaluation).where(Evaluation.application_id == app_id))
        audits = session.scalars(
            select(AuditLog).where(
                AuditLog.event == "llm.call",
                AuditLog.entity["id"].astext == str(run_id),
            )
        ).all()
        return ev.result, ev.stage_reached, [a.detail for a in audits]
    finally:
        session.close()


def test_borderline_candidate_gets_three_audited_votes(db_url, monkeypatch):
    from senthire.config import Settings
    from senthire.workers.tasks import screen as screen_tasks

    run_id, app_id, _ = _seed_run(["borderline_hard_filter"])
    disagreeing = [
        [_judgment("R1", "met", 1.0), _judgment("R2", "met", 1.0)],
        [_judgment("R1", "met", 1.0), _judgment("R2", "met", 1.0)],
        [_judgment("R1", "met", 1.0), _judgment("R2", "not_met", 0.0)],
    ]
    fake_deep, temperatures = _scripted_deep(disagreeing)
    monkeypatch.setattr(screen_tasks, "deep_analyze", fake_deep)

    outcome = screen_tasks.deep_application(str(run_id), str(app_id))

    assert outcome["status"] == "deep_done"
    hot = Settings(_env_file=None).deep_vote_temperature
    assert temperatures == [None, hot, hot], "vote 1 standard, votes 2..K sample hot"

    result, stage, audits = _reload(run_id, app_id)
    assert stage == "deep"
    assert result["deep_votes"]["completed"] == 3
    assert result["deep_votes"]["flagged"] == ["R2"]
    assert result["needs_review"] is True
    assert "deep_vote_disagreement" in result["review_reasons"]
    verdicts = {r["req_id"]: r["verdict"] for r in result["requirements"]}
    assert verdicts["R2"] == "met", "majority verdict stands; the flag routes a human"
    deep_audits = [a for a in audits if a["stage"] == "deep"]
    assert len(deep_audits) == 3, "every vote must reach the cost ledger"
    assert all(a["input_tokens"] == 200 for a in deep_audits)


def test_decision_band_candidate_keeps_the_single_pass(db_url, monkeypatch):
    from senthire.workers.tasks import screen as screen_tasks

    run_id, app_id, _ = _seed_run(["decision_band"])
    single = [[_judgment("R1", "met", 1.0), _judgment("R2", "partially_met", 0.5)]]
    fake_deep, temperatures = _scripted_deep(single)
    monkeypatch.setattr(screen_tasks, "deep_analyze", fake_deep)

    outcome = screen_tasks.deep_application(str(run_id), str(app_id))

    assert outcome["status"] == "deep_done"
    assert temperatures == [None], "no uncertainty reason → exactly one pass"
    result, stage, audits = _reload(run_id, app_id)
    assert stage == "deep"
    assert "deep_votes" not in result
    assert "deep_vote_disagreement" not in result.get("review_reasons", [])
    assert len([a for a in audits if a["stage"] == "deep"]) == 1
