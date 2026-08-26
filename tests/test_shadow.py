"""Shadow re-evaluation must mirror the pipeline's judgment layer exactly,
report only real differences, and never write anything.

Needs Postgres (citext + vector); skipped when SENTHIRE_TEST_DATABASE_URL is
unset, like the other database-backed suites.
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

ADMIN_URL = os.environ.get("SENTHIRE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not ADMIN_URL, reason="set SENTHIRE_TEST_DATABASE_URL to run shadow tests"
)


@pytest.fixture(scope="module")
def db_url():
    name = f"senthire_shadow_{uuid.uuid4().hex[:12]}"
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


# --------------------------------------------------------------------------- #
# A tiny but real spec: one deterministic knockout + two semantic requirements
# --------------------------------------------------------------------------- #

RAW_TEXT = (
    "Deneyim: Python ile bes yil arka uc gelistirme. "
    "Satis ekibiyle birlikte CRM entegrasyonlari kurdu. "
    "Takim liderligi: iki kisilik ekibi yonetti."
)


def make_spec():
    from senthire.domain.spec import (
        DeterministicCheck,
        EvaluationSpec,
        Requirement,
        SemanticCheck,
    )

    return EvaluationSpec(
        requirements=[
            Requirement(
                req_id="H1",
                category="relevant_experience",
                type="hard",
                evaluator="deterministic",
                deterministic=DeterministicCheck(
                    predicate={
                        "field": "derived.total_experience_months",
                        "op": ">=",
                        "value": 36,
                    }
                ),
            ),
            Requirement(
                req_id="R1",
                category="relevant_experience",
                type="scored",
                evaluator="semantic",
                semantic=SemanticCheck(rubric="backend development depth"),
            ),
            Requirement(
                req_id="R2",
                category="skills",
                type="scored",
                evaluator="semantic",
                semantic=SemanticCheck(rubric="CRM integration experience"),
            ),
        ]
    )


def profile_for(marker: str, months: int) -> dict:
    return {"marker": marker, "derived": {"total_experience_months": months}}


def light_output(r1: tuple[str, float | None], r2: tuple[str, float | None]):
    from senthire.screening.schemas import EvidenceQuote, LightScreenOutput, ReqJudgment

    def judgment(req_id: str, pair):
        verdict, score = pair
        return ReqJudgment(
            req_id=req_id,
            verdict=verdict,
            score=score,
            confidence=0.9,
            info_status="explicit" if verdict != "unknown" else "missing",
            evidence=[]
            if verdict == "unknown"
            else [EvidenceQuote(quote="Python ile bes yil arka uc gelistirme")],
            reasoning="test judgment",
        )

    return LightScreenOutput(judgments=[judgment("R1", r1), judgment("R2", r2)])


FAKE_USAGE_TOKENS = (111, 47)


def fake_usage():
    from senthire.screening.llm import LlmUsage

    return LlmUsage(
        model="fake-model",
        input_tokens=FAKE_USAGE_TOKENS[0],
        output_tokens=FAKE_USAGE_TOKENS[1],
        cache_read_tokens=0,
        cache_write_tokens=0,
    )


def stored_evaluation_parts(spec, profile, output, *, human_r2=None):
    """Build a stored result document exactly the way the light stage does."""
    from senthire.domain.scoring import RequirementVerdict
    from senthire.domain.scoring import score as run_scorer
    from senthire.screening.assemble import (
        build_result_document,
        judgments_to_verdicts,
        merge_verdicts,
    )
    from senthire.screening.deterministic import run_deterministic_stage
    from senthire.screening.evidence import verify_all

    det = run_deterministic_stage(spec, profile)
    if det.knocked_out and not det.borderline:
        verdicts = merge_verdicts(spec, det.verdicts, None)
        sr = run_scorer(spec, verdicts)
        return build_result_document(spec, verdicts, sr, stage_reached="hard_filter"), sr, "hard_filter"

    judgments, _ = verify_all(output.judgments, RAW_TEXT + "\n" + str(profile))
    light_verdicts = judgments_to_verdicts(judgments, "light")
    verdicts = merge_verdicts(spec, det.verdicts, light_verdicts)
    if human_r2 is not None:
        verdict, score = human_r2
        verdicts["R2"] = RequirementVerdict(
            req_id="R2",
            verdict=verdict,
            score=score,
            confidence=1.0,
            info_status="explicit",
            source_stage="human",
            reasoning="recruiter correction",
        )
    sr = run_scorer(spec, verdicts)
    return build_result_document(spec, verdicts, sr, stage_reached="light"), sr, "light"


@pytest.fixture()
def make_run(db_url):
    """Create a completed run with the given candidates; returns ids + outputs.

    candidates: marker -> dict(months=..., light=..., human_r2=..., deep=bool)
    """

    def factory(candidates: dict):
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
        from senthire.domain.ranking import rank_key

        spec = make_spec()
        session = get_sessionmaker()()
        org = Organization(name="Shadow Test A.S.")
        session.add(org)
        session.flush()
        job = Job(org_id=org.id, title="Backend Developer", status="active")
        session.add(job)
        session.flush()
        spec_row = EvaluationSpecRow(
            org_id=org.id, job_id=job.id, version=1, status="confirmed",
            spec=spec.model_dump(),
        )
        session.add(spec_row)
        session.flush()
        run = ScreeningRun(
            org_id=org.id, job_id=job.id, spec_id=spec_row.id,
            mode="interactive", status="complete",
            funnel={"versions": {"prompts": {"light": "baseline_v0"}}},
        )
        session.add(run)
        session.flush()

        evaluations = {}
        outputs = {}
        for marker, cfg in candidates.items():
            months = cfg.get("months", 48)
            profile = profile_for(marker, months)
            output = cfg.get("light") or light_output(("met", 1.0), ("partially_met", 0.5))
            outputs[marker] = output

            candidate = Candidate(org_id=org.id, display_name=f"Candidate {marker}")
            session.add(candidate)
            session.flush()
            document = Document(
                org_id=org.id, candidate_id=candidate.id, upload_job_id=job.id,
                sha256=uuid.uuid4().hex, s3_key=f"cv/{marker}", mime="application/pdf",
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

            doc, sr, stage = stored_evaluation_parts(
                spec, profile, output, human_r2=cfg.get("human_r2")
            )
            if cfg.get("deep"):
                stage = "deep"  # verdict sources stay light; stage marks escalation
            gate_fail = sr.gate.status != "pass"
            ev = Evaluation(
                org_id=org.id, run_id=run.id, application_id=application.id,
                profile_version=1, spec_version=1, pipeline_version="p1",
                stage_reached=stage,
                hard_result="fail" if gate_fail else "pass",
                overall_score=sr.final_score,
                band="rejected" if gate_fail else sr.band,
                confidence=sr.confidence,
                result=doc,
            )
            session.add(ev)
            session.flush()
            evaluations[marker] = ev

        ranked = sorted(
            (ev for ev in evaluations.values() if ev.band != "rejected"),
            key=lambda ev: rank_key(ev.overall_score, ev.confidence, ev.application_id),
        )
        for position, ev in enumerate(ranked, start=1):
            ev.rank = position
        session.commit()

        app_ids = {marker: str(ev.application_id) for marker, ev in evaluations.items()}
        session.close()
        return run.id, app_ids, outputs, spec

    return factory


def patched_light(monkeypatch, responses: dict, calls: dict):
    """Route shadow's light calls through canned outputs, counting by marker."""

    def fake_light(spec, profile):
        marker = profile["marker"]
        calls[marker] = calls.get(marker, 0) + 1
        return responses[marker], fake_usage()

    import senthire.evals.shadow as shadow

    monkeypatch.setattr(shadow, "light_screen", fake_light)


def run_shadow(run_id, **kwargs):
    from senthire.db.session import get_sessionmaker
    from senthire.evals.shadow import shadow_run

    session = get_sessionmaker()()
    try:
        report = shadow_run(session, run_id, **kwargs)
        assert not session.new and not session.dirty and not session.deleted, (
            "shadow must never stage a database write"
        )
    finally:
        session.close()
    return report


def by_app(report, app_ids, marker):
    return next(c for c in report.candidates if c.application_id == app_ids[marker])


def test_identical_stack_reports_no_changes(make_run, monkeypatch):
    run_id, app_ids, outputs, _ = make_run(
        {"A": {}, "B": {"light": light_output(("partially_met", 0.5), ("met", 1.0))}}
    )
    calls: dict = {}
    patched_light(monkeypatch, outputs, calls)

    report = run_shadow(run_id)
    summary = report.summary()
    assert summary["verdict_changes"] == 0
    assert summary["gate_flips"] == 0
    assert summary["band_moves"] == 0
    assert summary["rank_moves"] == 0
    assert summary["errors"] == 0
    assert calls == {"A": 1, "B": 1}
    assert report.unchanged > 0
    a = by_app(report, app_ids, "A")
    assert a.shadow_score == a.stored_score and a.shadow_band == a.stored_band
    # cost accounting flows through
    assert summary["calls"] == 2
    assert summary["tokens"]["input"] == 2 * FAKE_USAGE_TOKENS[0]


def test_knocked_out_candidate_costs_no_model_call(make_run, monkeypatch):
    run_id, app_ids, outputs, _ = make_run({"A": {}, "KO": {"months": 12}})
    calls: dict = {}
    patched_light(monkeypatch, outputs, calls)

    report = run_shadow(run_id)
    assert calls == {"A": 1}, "hard-filtered candidates must not spend tokens"
    ko = by_app(report, app_ids, "KO")
    assert ko.comparable and ko.stored_gate_fail and ko.shadow_gate_fail
    assert not ko.gate_flip
    assert report.summary()["verdict_changes"] == 0


def test_changed_verdict_is_reported_with_both_sides(make_run, monkeypatch):
    run_id, app_ids, outputs, _ = make_run({"A": {}})
    shadow_outputs = dict(outputs)
    shadow_outputs["A"] = light_output(("met", 1.0), ("met", 1.0))  # R2 improves
    calls: dict = {}
    patched_light(monkeypatch, shadow_outputs, calls)

    report = run_shadow(run_id)
    a = by_app(report, app_ids, "A")
    assert len(a.verdict_diffs) == 1
    diff = a.verdict_diffs[0]
    assert diff["req_id"] == "R2"
    assert (diff["from"], diff["to"]) == ("partially_met", "met")
    assert diff["stored_source"] == "light" and diff["shadow_source"] == "light"
    assert a.shadow_score is not None and a.stored_score is not None
    assert a.shadow_score > a.stored_score


def test_human_correction_is_pinned_not_relitigated(make_run, monkeypatch):
    run_id, app_ids, _outputs, _ = make_run({"C": {"human_r2": ("not_met", 0.0)}})
    # The model would now say R2 is met — but a recruiter already ruled.
    shadow_outputs = {"C": light_output(("met", 1.0), ("met", 1.0))}
    calls: dict = {}
    patched_light(monkeypatch, shadow_outputs, calls)

    report = run_shadow(run_id)
    c = by_app(report, app_ids, "C")
    assert c.human_pinned == ["R2"]
    assert all(d["req_id"] != "R2" for d in c.verdict_diffs)
    assert c.shadow_score == c.stored_score, "the human verdict carries into the shadow score"


def test_deep_candidates_skipped_without_deep_flag(make_run, monkeypatch):
    run_id, app_ids, outputs, _ = make_run({"A": {}, "D": {"deep": True}})
    calls: dict = {}
    patched_light(monkeypatch, outputs, calls)

    report = run_shadow(run_id)
    d = by_app(report, app_ids, "D")
    assert not d.comparable
    assert "--deep" in (d.skipped_reason or "")
    assert calls == {"A": 1}, "skipped candidates must not spend tokens"


def test_deep_flag_reruns_deep_stage(make_run, monkeypatch):
    from senthire.screening.schemas import DeepAnalysisOutput

    run_id, app_ids, outputs, _ = make_run({"D": {"deep": True}})
    calls: dict = {}
    patched_light(monkeypatch, outputs, calls)

    deep_calls = {"n": 0}

    def fake_deep(spec, profile, raw_text, light_judgments):
        deep_calls["n"] += 1
        assert raw_text == RAW_TEXT
        assert all(row["source_stage"] == "light" for row in light_judgments)
        return DeepAnalysisOutput(judgments=outputs["D"].judgments), fake_usage()

    import senthire.evals.shadow as shadow

    monkeypatch.setattr(shadow, "deep_analyze", fake_deep)

    report = run_shadow(run_id, deep=True)
    assert deep_calls["n"] == 1
    d = by_app(report, app_ids, "D")
    assert d.comparable and not d.verdict_diffs
    assert report.summary()["verdict_changes"] == 0


def test_rank_moves_are_reported(make_run, monkeypatch):
    run_id, app_ids, _outputs, _ = make_run(
        {
            "A": {"light": light_output(("met", 1.0), ("met", 1.0))},
            "B": {"light": light_output(("partially_met", 0.5), ("partially_met", 0.5))},
        }
    )
    # Under the "new stack", B leaps past A.
    shadow_outputs = {
        "A": light_output(("partially_met", 0.5), ("partially_met", 0.5)),
        "B": light_output(("met", 1.0), ("met", 1.0)),
    }
    calls: dict = {}
    patched_light(monkeypatch, shadow_outputs, calls)

    report = run_shadow(run_id)
    moves = {m["application_id"]: m for m in report.rank_moves}
    assert moves[app_ids["A"]]["stored_rank"] == 1
    assert moves[app_ids["A"]]["shadow_rank"] == 2
    assert moves[app_ids["B"]]["shadow_rank"] == 1


def test_report_carries_version_stamps(make_run, monkeypatch):
    run_id, _app_ids, outputs, _ = make_run({"A": {}})
    patched_light(monkeypatch, outputs, {})

    report = run_shadow(run_id)
    assert report.baseline_versions == {"prompts": {"light": "baseline_v0"}}
    assert report.shadow_versions["prompts"]["light"]
    assert report.shadow_versions["vocabulary"]
    payload = report.to_json()
    assert payload["frozen_layers"] == ["extraction", "normalization"]
