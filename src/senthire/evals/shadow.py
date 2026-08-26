"""Shadow re-evaluation: what would this finished run look like under the
CURRENT stack?

Before switching a model tier or shipping a bumped prompt, re-run a real
run's judgment layer with the new configuration and read the diff — verdict
by verdict, gate by gate, rank by rank — instead of hoping. Nothing is
persisted: the stored run stays exactly as the recruiter saw it.

    python -m senthire.evals.shadow RUN_ID              # light stage
    python -m senthire.evals.shadow RUN_ID --deep       # + deep, where reached
    python -m senthire.evals.shadow RUN_ID --limit 10   # top of the list only
    python -m senthire.evals.shadow RUN_ID --json out.json

Scope, honestly stated: the extraction and normalization layers are FROZEN —
the stored profile and raw text are reused as-is, so the diff isolates the
judgment layer (prompts, models, scorer). Human-corrected verdicts are
pinned: a recruiter's decision is ground truth, not drift, so it is carried
into the shadow score and excluded from the diff.
"""

import argparse
import json
import sys
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select

from senthire.config import get_settings
from senthire.db.models import Application, Evaluation
from senthire.db.session import get_sessionmaker
from senthire.domain.ranking import rank_key
from senthire.domain.scoring import RequirementVerdict
from senthire.domain.scoring import score as run_scorer
from senthire.domain.spec import EvaluationSpec
from senthire.screening.assemble import (
    judgments_to_verdicts,
    merge_verdicts,
    verdicts_from_result_document,
)
from senthire.screening.deterministic import run_deterministic_stage
from senthire.screening.evidence import verify_all
from senthire.screening.llm import deep_analyze, light_screen
from senthire.screening.pricing import estimate_usd
from senthire.workers.tasks.screen import (
    _load_run_context,
    _profile_for_application,
    run_versions,
)

# Verdict sources the shadow's light pass can honestly be compared against.
LIGHT_COMPARABLE = {"deterministic", "light"}


@dataclass
class CandidateShadow:
    application_id: str
    stored_rank: int | None
    stored_stage: str
    stored_score: float | None
    stored_band: str | None
    stored_gate_fail: bool
    shadow_score: float | None = None
    shadow_band: str | None = None
    shadow_gate_fail: bool = False
    shadow_confidence: float | None = None
    comparable: bool = True
    skipped_reason: str | None = None
    verdict_diffs: list[dict] = field(default_factory=list)
    human_pinned: list[str] = field(default_factory=list)
    deep_only: list[str] = field(default_factory=list)  # stored deep verdicts, light-only shadow

    @property
    def gate_flip(self) -> bool:
        return self.comparable and self.stored_gate_fail != self.shadow_gate_fail

    @property
    def band_move(self) -> bool:
        return self.comparable and (self.stored_band or "") != (self.shadow_band or "")


@dataclass
class ShadowReport:
    run_id: str
    stage: str  # light | deep
    baseline_versions: dict
    shadow_versions: dict
    fake_models: bool
    candidates: list[CandidateShadow] = field(default_factory=list)
    rank_moves: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    usd_estimate: float = 0.0
    unchanged: int = 0

    def summary(self) -> dict:
        comparable = [c for c in self.candidates if c.comparable]
        return {
            "candidates": len(self.candidates),
            "comparable": len(comparable),
            "verdict_changes": sum(len(c.verdict_diffs) for c in comparable),
            "gate_flips": sum(1 for c in comparable if c.gate_flip),
            "band_moves": sum(1 for c in comparable if c.band_move),
            "rank_moves": len(self.rank_moves),
            "human_pinned": sum(len(c.human_pinned) for c in self.candidates),
            "errors": len(self.errors),
            "calls": self.calls,
            "tokens": {
                "input": self.input_tokens,
                "output": self.output_tokens,
                "cache_read": self.cache_read_tokens,
            },
            "usd_estimate": round(self.usd_estimate, 6),
        }

    def to_json(self) -> dict:
        return {
            "run_id": self.run_id,
            "stage": self.stage,
            "baseline_versions": self.baseline_versions,
            "shadow_versions": self.shadow_versions,
            "frozen_layers": ["extraction", "normalization"],
            "fake_models": self.fake_models,
            "summary": self.summary(),
            "rank_moves": self.rank_moves,
            "errors": self.errors,
            "candidates": [
                {
                    "application_id": c.application_id,
                    "stored": {
                        "rank": c.stored_rank,
                        "stage": c.stored_stage,
                        "score": c.stored_score,
                        "band": c.stored_band,
                        "gate_fail": c.stored_gate_fail,
                    },
                    "shadow": {
                        "score": c.shadow_score,
                        "band": c.shadow_band,
                        "gate_fail": c.shadow_gate_fail,
                    },
                    "comparable": c.comparable,
                    "skipped_reason": c.skipped_reason,
                    "verdict_diffs": c.verdict_diffs,
                    "human_pinned": c.human_pinned,
                    "deep_only": c.deep_only,
                }
                for c in self.candidates
            ],
        }


def _light_judgment_rows(verdicts: dict[str, RequirementVerdict]) -> list[dict]:
    """The first-pass block the deep prompt expects: same shape the production
    path feeds it (result-document requirement rows with source_stage light)."""
    return [
        {
            "req_id": v.req_id,
            "verdict": v.verdict,
            "score": v.score,
            "confidence": v.confidence,
            "info_status": v.info_status,
            "evidence": v.evidence or [],
            "reasoning": v.reasoning,
            "source_stage": "light",
        }
        for v in verdicts.values()
        if v.source_stage == "light"
    ]


def _track(report: ShadowReport, usage) -> None:
    if usage is None:
        return
    report.calls += 1
    report.input_tokens += usage.input_tokens
    report.output_tokens += usage.output_tokens
    report.cache_read_tokens += usage.cache_read_tokens
    report.usd_estimate += estimate_usd(
        {
            "model": usage.model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "cache_write_tokens": usage.cache_write_tokens,
        }
    )


def _shadow_verdicts(
    spec: EvaluationSpec, profile_row, ev, *, deep: bool, report: ShadowReport
) -> dict[str, RequirementVerdict]:
    """Re-run the judgment layer exactly the way the pipeline runs it,
    without touching the database."""
    det = run_deterministic_stage(spec, profile_row.profile)

    light_verdicts = None
    if not (det.knocked_out and not det.borderline):
        output, usage = light_screen(spec, profile_row.profile)
        _track(report, usage)
        source_text = profile_row.raw_text + "\n" + str(profile_row.profile)
        judgments, _ = verify_all(output.judgments, source_text)
        light_verdicts = judgments_to_verdicts(judgments, "light")

    verdicts = merge_verdicts(spec, det.verdicts, light_verdicts)

    if deep and ev.stage_reached == "deep" and light_verdicts:
        deep_out, deep_usage = deep_analyze(
            spec,
            profile_row.profile,
            profile_row.raw_text,
            _light_judgment_rows(verdicts),
        )
        _track(report, deep_usage)
        deep_judgments, _ = verify_all(deep_out.judgments, profile_row.raw_text)
        deep_verdicts = judgments_to_verdicts(deep_judgments, "deep")
        verdicts = merge_verdicts(spec, det.verdicts, light_verdicts, deep_verdicts)

    # A recruiter's correction outranks every machine stage (docs/06 §5) and
    # would survive a real re-run the same way — carry it, don't re-litigate it.
    for rid, stored in verdicts_from_result_document(ev.result).items():
        if stored.source_stage == "human":
            verdicts[rid] = stored
    return verdicts


def shadow_run(session, run_id: uuid.UUID, *, deep: bool = False, limit: int | None = None):
    settings = get_settings()
    run, spec = _load_run_context(session, run_id)
    if run is None:
        raise SystemExit(f"run {run_id} not found")
    if run.status != "complete":
        raise SystemExit(f"run {run_id} is '{run.status}' — shadow only re-reads finished runs")

    report = ShadowReport(
        run_id=str(run_id),
        stage="deep" if deep else "light",
        baseline_versions=(run.funnel or {}).get("versions") or {"recorded": False},
        shadow_versions=run_versions(),
        fake_models=settings.fake_models,
    )

    query = (
        select(Evaluation)
        .where(Evaluation.run_id == run_id)
        .order_by(Evaluation.rank.asc().nulls_last(), Evaluation.application_id)
    )
    if limit:
        query = query.limit(limit)
    evaluations = session.scalars(query).all()

    scored: list[tuple[CandidateShadow, float | None, float | None]] = []
    for ev in evaluations:
        cand = CandidateShadow(
            application_id=str(ev.application_id),
            stored_rank=ev.rank,
            stored_stage=ev.stage_reached,
            stored_score=ev.overall_score,
            stored_band=ev.band,
            stored_gate_fail=ev.band == "rejected" or ev.hard_result == "fail",
        )
        report.candidates.append(cand)

        if ev.stage_reached == "deep" and not deep:
            # Comparing a light-only shadow against a deep-corrected stored
            # result would blame the stage difference on the version change.
            cand.comparable = False
            cand.skipped_reason = "reached deep; re-run with --deep to compare"
            stored = verdicts_from_result_document(ev.result)
            cand.deep_only = sorted(r for r, v in stored.items() if v.source_stage == "deep")
            continue

        app = session.get(Application, ev.application_id)
        profile_row = _profile_for_application(session, app)
        if profile_row is None:
            cand.comparable = False
            cand.skipped_reason = "profile row missing"
            continue

        try:
            verdicts = _shadow_verdicts(spec, profile_row, ev, deep=deep, report=report)
        except Exception as exc:  # a single bad candidate must not kill the report
            cand.comparable = False
            cand.skipped_reason = f"shadow failed: {exc}"
            report.errors.append(f"{ev.application_id}: {exc}")
            continue

        stored = verdicts_from_result_document(ev.result)
        for rid in sorted(set(stored) | set(verdicts)):
            sv, hv = stored.get(rid), verdicts.get(rid)
            if sv is not None and sv.source_stage == "human":
                cand.human_pinned.append(rid)
                continue
            if sv is None or hv is None:
                continue
            if not deep and sv.source_stage not in LIGHT_COMPARABLE:
                cand.deep_only.append(rid)
                continue
            if sv.verdict != hv.verdict or sv.score != hv.score:
                cand.verdict_diffs.append(
                    {
                        "req_id": rid,
                        "from": sv.verdict,
                        "to": hv.verdict,
                        "from_score": sv.score,
                        "to_score": hv.score,
                        "stored_source": sv.source_stage,
                        "shadow_source": hv.source_stage,
                    }
                )
            else:
                report.unchanged += 1

        sr = run_scorer(spec, verdicts)
        cand.shadow_score = sr.final_score
        cand.shadow_gate_fail = sr.gate.status != "pass"
        # Speak the same language the finalizer writes into evaluations:
        # a gate-failed candidate's band is "rejected", not its raw band.
        cand.shadow_band = "rejected" if cand.shadow_gate_fail else sr.band
        cand.shadow_confidence = sr.confidence
        scored.append((cand, sr.final_score, sr.confidence))

    # Rank comparison within the comparable, shadow-gate-passing cohort.
    passing = [(c, s, conf) for c, s, conf in scored if not c.shadow_gate_fail and s is not None]
    passing.sort(key=lambda t: rank_key(t[1], t[2], t[0].application_id))
    for position, (cand, _, _) in enumerate(passing, start=1):
        if cand.stored_rank is not None and cand.stored_rank != position:
            report.rank_moves.append(
                {
                    "application_id": cand.application_id,
                    "stored_rank": cand.stored_rank,
                    "shadow_rank": position,
                }
            )
    return report


def _print_report(report: ShadowReport) -> None:
    s = report.summary()
    print(f"shadow of run {report.run_id} — stage: {report.stage}"
          + (" (FAKE MODELS)" if report.fake_models else ""))
    print(f"  baseline: {json.dumps(report.baseline_versions, ensure_ascii=False)}")
    print(f"  shadow:   {json.dumps(report.shadow_versions, ensure_ascii=False)}")
    print("  frozen: extraction + normalization (stored profiles reused)")
    for cand in report.candidates:
        if not cand.comparable:
            print(f"  - {cand.application_id[:8]}  skipped: {cand.skipped_reason}")
            continue
        marker = "≠" if (cand.verdict_diffs or cand.gate_flip or cand.band_move) else "="
        line = (
            f"  {marker} {cand.application_id[:8]}  "
            f"score {cand.stored_score} → {cand.shadow_score}  "
            f"band {cand.stored_band} → {cand.shadow_band}"
        )
        if cand.gate_flip:
            line += "  GATE FLIP"
        print(line)
        for diff in cand.verdict_diffs:
            print(
                f"      {diff['req_id']}: {diff['from']} → {diff['to']}"
                f"  (score {diff['from_score']} → {diff['to_score']},"
                f" stored source {diff['stored_source']})"
            )
    print(
        f"  summary: {s['verdict_changes']} verdict change(s) over "
        f"{s['comparable']}/{s['candidates']} candidates, {s['gate_flips']} gate flip(s), "
        f"{s['band_moves']} band move(s), {s['rank_moves']} rank move(s)"
    )
    print(
        f"  cost: {s['calls']} call(s), in={s['tokens']['input']} out={s['tokens']['output']} "
        f"cache_read={s['tokens']['cache_read']} ≈ ${s['usd_estimate']}"
    )
    for error in report.errors:
        print(f"  ! {error}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m senthire.evals.shadow")
    parser.add_argument("run_id", help="a completed screening run's id")
    parser.add_argument("--deep", action="store_true",
                        help="also re-run deep analysis where the run reached it")
    parser.add_argument("--limit", type=int, default=None,
                        help="only the N best-ranked candidates")
    parser.add_argument("--json", default=None, help="write the full report to this path")
    args = parser.parse_args()

    session = get_sessionmaker()()
    try:
        report = shadow_run(
            session, uuid.UUID(args.run_id), deep=args.deep, limit=args.limit
        )
    finally:
        session.close()

    _print_report(report)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report.to_json(), fh, ensure_ascii=False, indent=2)
        print(f"report written to {args.json}")
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
