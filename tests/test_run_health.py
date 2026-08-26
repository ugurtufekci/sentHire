"""Stall detection and recovery must re-issue exactly the missing messages,
never duplicate finished work, and turn the one true race into a handled
"already evaluated" instead of a duplicate ranking row.

Needs Postgres; skipped when SENTHIRE_TEST_DATABASE_URL is unset.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, text

ADMIN_URL = os.environ.get("SENTHIRE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not ADMIN_URL, reason="set SENTHIRE_TEST_DATABASE_URL to run run-health tests"
)

RAW_TEXT = "Deneyim: Python ile bes yil arka uc gelistirme."


@pytest.fixture(scope="module")
def db_url():
    name = f"senthire_health_{uuid.uuid4().hex[:12]}"
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
                evaluator="semantic", semantic=SemanticCheck(rubric="depth"),
            )
        ]
    )


@pytest.fixture()
def seed(db_url):
    """Create a run in a chosen status with N applications; `evaluated` of
    them already carry an evaluation (stage_reached per `stages`)."""

    def factory(status, *, apps=1, evaluated=0, stages=None, mode="interactive", funnel=None):
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

        session = get_sessionmaker()()
        org = Organization(name="Health Test A.S.")
        session.add(org)
        session.flush()
        job = Job(org_id=org.id, title="Backend", status="active")
        session.add(job)
        session.flush()
        spec_row = EvaluationSpecRow(
            org_id=org.id, job_id=job.id, version=1, status="confirmed",
            spec=_spec().model_dump(),
        )
        session.add(spec_row)
        session.flush()

        app_ids = []
        for index in range(apps):
            candidate = Candidate(org_id=org.id, display_name=f"Aday {index}")
            session.add(candidate)
            session.flush()
            document = Document(
                org_id=org.id, candidate_id=candidate.id, upload_job_id=job.id,
                sha256=uuid.uuid4().hex, s3_key=f"cv/{index}", mime="application/pdf",
                parse_status="parsed",
            )
            session.add(document)
            session.flush()
            application = Application(
                org_id=org.id, job_id=job.id, candidate_id=candidate.id,
                document_id=document.id, status="profiled",
            )
            session.add(application)
            session.flush()
            session.add(
                CandidateProfileRow(
                    org_id=org.id, document_id=document.id, candidate_id=candidate.id,
                    version=1, profile={"derived": {"total_experience_months": 48}},
                    raw_text=RAW_TEXT,
                )
            )
            app_ids.append(application.id)

        run = ScreeningRun(
            org_id=org.id, job_id=job.id, spec_id=spec_row.id,
            mode=mode, status=status, funnel=funnel or {},
        )
        session.add(run)
        session.flush()

        for index in range(evaluated):
            session.add(
                Evaluation(
                    org_id=org.id, run_id=run.id, application_id=app_ids[index],
                    profile_version=1, spec_version=1, pipeline_version="p1",
                    stage_reached=(stages or {}).get(index, "light"),
                    hard_result="pass", result={},
                )
            )
        session.commit()
        run_id, org_id = run.id, org.id
        session.close()
        return run_id, app_ids, org_id

    return factory


@pytest.fixture()
def delays(monkeypatch):
    """Capture every task enqueue instead of touching a broker."""
    from senthire.workers.tasks import screen as screen_tasks

    calls: dict[str, list[tuple]] = {}

    def recorder(name):
        def delay(*args, **kwargs):
            calls.setdefault(name, []).append(args)

        return delay

    for name in (
        "run_start", "screen_application", "finalize_run",
        "deep_application", "score_run", "poll_batch",
    ):
        monkeypatch.setattr(getattr(screen_tasks, name), "delay", recorder(name))
    return calls


def _recover(run_id):
    from senthire.db.models import ScreeningRun
    from senthire.db.session import get_sessionmaker
    from senthire.services.run_health import recover

    session = get_sessionmaker()()
    try:
        run = session.get(ScreeningRun, run_id)
        return recover(session, run)
    finally:
        session.close()


def _run_row(run_id):
    from senthire.db.models import ScreeningRun
    from senthire.db.session import get_sessionmaker

    session = get_sessionmaker()()
    try:
        return session.get(ScreeningRun, run_id)
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# stall detection (pure)
# --------------------------------------------------------------------------- #


def _stub_run(status, mode="interactive", funnel=None):
    from types import SimpleNamespace

    return SimpleNamespace(status=status, mode=mode, funnel=funnel or {})


def test_stall_thresholds():
    from senthire.config import Settings, get_settings
    from senthire.services.run_health import is_stalled

    settings = Settings(_env_file=None)
    assert get_settings().run_stall_after_seconds == settings.run_stall_after_seconds
    now = datetime.now(UTC)
    fresh = now - timedelta(seconds=30)
    silent = now - timedelta(seconds=settings.run_stall_after_seconds + 60)

    assert not is_stalled(_stub_run("screening"), fresh, now)
    assert is_stalled(_stub_run("screening"), silent, now)
    assert is_stalled(_stub_run("queued"), silent, now)
    assert not is_stalled(_stub_run("complete"), silent, now)
    assert not is_stalled(_stub_run("failed"), silent, now)
    assert is_stalled(_stub_run("screening"), None, now), "clockless active run must surface"


def test_batch_wait_gets_the_longer_budget():
    from senthire.config import Settings
    from senthire.services.run_health import is_stalled

    settings = Settings(_env_file=None)
    now = datetime.now(UTC)
    batch_funnel = {"batch": {"light": {"id": "b1"}}}
    quiet_for_interactive = now - timedelta(seconds=settings.run_stall_after_seconds + 60)
    quiet_for_batch = now - timedelta(seconds=settings.batch_stall_after_seconds + 60)

    run = _stub_run("screening", mode="batch", funnel=batch_funnel)
    assert not is_stalled(run, quiet_for_interactive, now), "a waiting batch is not stalled"
    assert is_stalled(run, quiet_for_batch, now), "a dead poll chain is"
    # batch mode without a submitted batch waits on OUR workers — short budget
    assert is_stalled(_stub_run("screening", mode="batch"), quiet_for_interactive, now)


def test_last_activity_prefers_the_newest_signal(seed):
    from senthire.db.models import ScreeningRun
    from senthire.db.session import get_sessionmaker
    from senthire.services.run_health import last_activity_at

    run_id, _, _ = seed("screening", apps=1, evaluated=1)
    session = get_sessionmaker()()
    try:
        run = session.get(ScreeningRun, run_id)
        activity = last_activity_at(session, run)
        assert activity is not None and activity.tzinfo is not None

        stamp = datetime.now(UTC).isoformat()
        run.funnel = {"batch": {"light": {"id": "b1", "last_poll_at": stamp}}}
        session.commit()
        assert last_activity_at(session, run).isoformat() == stamp
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# recovery per status
# --------------------------------------------------------------------------- #


def test_queued_reissues_run_start(seed, delays):
    run_id, _, _ = seed("queued")
    actions = _recover(run_id)
    assert actions == ["run_start_reissued"]
    assert delays["run_start"] == [(str(run_id),)]


def test_screening_reissues_only_the_missing_candidates(seed, delays):
    run_id, app_ids, _ = seed("screening", apps=3, evaluated=1, funnel={"total": 3})
    actions = _recover(run_id)
    assert actions == ["screen_reissued:2"]
    sent = {args[1] for args in delays["screen_application"]}
    assert sent == {str(app_ids[1]), str(app_ids[2])}


def test_screening_with_everything_done_advances_the_phase(seed, delays):
    run_id, _, _ = seed("screening", apps=2, evaluated=2, funnel={"total": 2})
    actions = _recover(run_id)
    assert actions == ["light_phase_check"]
    assert "screen_application" not in delays
    assert _run_row(run_id).status == "selecting"
    assert delays["finalize_run"] == [(str(run_id),)]


def test_selecting_reissues_finalize(seed, delays):
    run_id, _, _ = seed("selecting", apps=1, evaluated=1)
    assert _recover(run_id) == ["finalize_reissued"]
    assert delays["finalize_run"] == [(str(run_id),)]


def test_deep_analysis_reissues_only_pending(seed, delays):
    run_id, app_ids, _ = seed(
        "deep_analysis", apps=2, evaluated=2, stages={0: "deep", 1: "light"},
        funnel={"deep_pending": None},  # placeholder, set below
    )
    from senthire.db.models import ScreeningRun
    from senthire.db.session import get_sessionmaker

    session = get_sessionmaker()()
    run = session.get(ScreeningRun, run_id)
    run.funnel = {"deep_pending": [str(app_ids[0]), str(app_ids[1])], "total": 2}
    session.commit()
    session.close()

    actions = _recover(run_id)
    assert actions == ["deep_reissued:1"]
    assert delays["deep_application"] == [(str(run_id), str(app_ids[1]))]


def test_deep_analysis_with_everything_done_advances(seed, delays):
    run_id, app_ids, _ = seed(
        "deep_analysis", apps=1, evaluated=1, stages={0: "deep"}, funnel={"total": 1},
    )
    from senthire.db.models import ScreeningRun
    from senthire.db.session import get_sessionmaker

    session = get_sessionmaker()()
    run = session.get(ScreeningRun, run_id)
    run.funnel = {"deep_pending": [str(app_ids[0])], "total": 1}
    session.commit()
    session.close()

    actions = _recover(run_id)
    assert actions == ["deep_phase_check"]
    assert _run_row(run_id).status == "scoring"
    assert delays["score_run"] == [(str(run_id),)]


def test_scoring_reissues_score(seed, delays):
    run_id, _, _ = seed("scoring", apps=1, evaluated=1)
    assert _recover(run_id) == ["score_reissued"]
    assert delays["score_run"] == [(str(run_id),)]


def test_batch_screening_reissues_the_poll(seed, delays):
    run_id, _, _ = seed(
        "screening", mode="batch",
        funnel={"batch": {"light": {"id": "batch-abc", "submitted": 5}}},
    )
    assert _recover(run_id) == ["light_batch_poll_reissued"]
    assert delays["poll_batch"] == [(str(run_id), "light", "batch-abc")]


def test_terminal_runs_are_left_alone(seed, delays):
    run_id, _, _ = seed("complete", apps=1, evaluated=1)
    assert _recover(run_id) == []
    assert delays == {}


def test_recovery_leaves_an_audit_trail(seed, delays):
    from senthire.db.models import AuditLog
    from senthire.db.session import get_sessionmaker

    run_id, _, _ = seed("queued")
    _recover(run_id)
    session = get_sessionmaker()()
    try:
        entry = session.scalar(
            select(AuditLog).where(
                AuditLog.event == "run.recovered",
                AuditLog.entity["id"].astext == str(run_id),
            )
        )
        assert entry is not None
        assert entry.detail["actions"] == ["run_start_reissued"]
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# the database invariant behind safe re-kicks
# --------------------------------------------------------------------------- #


def test_duplicate_evaluation_is_refused_by_the_database(seed):
    from sqlalchemy.exc import IntegrityError

    from senthire.db.models import Evaluation, ScreeningRun
    from senthire.db.session import get_sessionmaker

    run_id, app_ids, org_id = seed("screening", apps=1, evaluated=1)
    session = get_sessionmaker()()
    try:
        session.add(
            Evaluation(
                org_id=org_id, run_id=run_id, application_id=app_ids[0],
                profile_version=1, spec_version=1, pipeline_version="p1",
                stage_reached="light", hard_result="pass", result={},
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
        run = session.get(ScreeningRun, run_id)
        assert run.created_at is not None, "migration 0011 must backfill the birth clock"
    finally:
        session.close()


def test_screen_application_race_resolves_to_already_evaluated(seed, delays, monkeypatch):
    """A recovery re-kick racing a still-alive worker: the loser's commit hits
    the unique constraint and must come back as already_evaluated, leaving
    exactly one row."""
    from senthire.db.models import Evaluation
    from senthire.db.session import get_sessionmaker
    from senthire.screening.schemas import EvidenceQuote, LightScreenOutput, ReqJudgment
    from senthire.workers.tasks import screen as screen_tasks

    run_id, app_ids, org_id = seed("screening", apps=1, funnel={"total": 1})

    def fake_light(spec, profile):
        from senthire.screening.llm import LlmUsage

        return (
            LightScreenOutput(
                judgments=[
                    ReqJudgment(
                        req_id="R1", verdict="met", score=1.0, confidence=0.9,
                        info_status="explicit",
                        evidence=[EvidenceQuote(quote="Python ile bes yil arka uc")],
                        reasoning="test",
                    )
                ]
            ),
            LlmUsage("fake", 10, 5, 0, 0),
        )

    monkeypatch.setattr(screen_tasks, "light_screen", fake_light)

    original_persist = screen_tasks._persist_light_evaluation

    def racing_persist(session, run, spec, app, profile_row, det, output, usage, light_failed):
        # The "other worker" lands its row between this worker's existence
        # check and its own insert.
        rival = get_sessionmaker()()
        rival.add(
            Evaluation(
                org_id=org_id, run_id=run_id, application_id=app.id,
                profile_version=1, spec_version=1, pipeline_version="p1",
                stage_reached="light", hard_result="pass", result={},
            )
        )
        rival.commit()
        rival.close()
        return original_persist(
            session, run, spec, app, profile_row, det, output, usage, light_failed
        )

    monkeypatch.setattr(screen_tasks, "_persist_light_evaluation", racing_persist)

    outcome = screen_tasks.screen_application(str(run_id), str(app_ids[0]))
    assert outcome == {"status": "already_evaluated"}

    session = get_sessionmaker()()
    try:
        rows = session.scalars(
            select(Evaluation).where(Evaluation.run_id == run_id)
        ).all()
        assert len(rows) == 1, "the race must never produce two ranking rows"
    finally:
        session.close()
