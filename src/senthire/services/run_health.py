"""Stall detection and idempotent recovery for screening runs (docs/08 §7).

Celery messages are hints, the database is the state machine (docs/02) — so
a lost message strands a run in a non-terminal status with nothing left to
advance it: "queued" whose start task vanished, "screening" with workers
gone mid-fan-out, "deep_analysis" whose enqueue loop died right after the
phase advanced, a batch whose poll chain broke. The UI shows a spinner
forever, which in front of a customer is indistinguishable from a broken
product.

Detection is read-side: every status has a progress clock (run birth, start,
latest evaluation, latest batch poll), and silence beyond the configured
budget marks the run stalled — no scheduler process needed, the check runs
when someone looks. Recovery re-issues exactly the messages that are
missing; every underlying task either guards on status or skips
already-done work, and the (run_id, application_id) uniqueness added in
migration 0011 turns the one true race (a re-kick passing a still-alive
worker) into a handled IntegrityError instead of a duplicate ranking row.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from senthire.config import get_settings
from senthire.db.models import Application, AuditLog, CandidateProfileRow, Evaluation, ScreeningRun

ACTIVE_STATUSES = {"queued", "screening", "selecting", "deep_analysis", "scoring"}

# Statuses in which a batch-mode run is waiting on the provider rather than
# on our own workers; their silence budget is the poll cadence.
BATCH_WAIT_STATUSES = {"screening", "deep_analysis"}


def _batch_info(run: ScreeningRun) -> dict | None:
    stage = "light" if run.status == "screening" else "deep"
    info = ((run.funnel or {}).get("batch") or {}).get(stage)
    return {**info, "stage": stage} if info else None


def _parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def last_activity_at(session: Session, run: ScreeningRun) -> datetime | None:
    """The newest sign of life this run has produced."""
    candidates = [run.created_at, run.started_at, run.finished_at]
    candidates.append(
        session.scalar(select(func.max(Evaluation.created_at)).where(Evaluation.run_id == run.id))
    )
    batch = _batch_info(run)
    if batch:
        candidates.append(_parse_iso(batch.get("last_poll_at")))
    known = [c for c in candidates if c is not None]
    return max(known) if known else None


def stall_budget_seconds(run: ScreeningRun) -> int:
    settings = get_settings()
    if run.mode == "batch" and run.status in BATCH_WAIT_STATUSES and _batch_info(run):
        return settings.batch_stall_after_seconds
    return settings.run_stall_after_seconds


def is_stalled(run: ScreeningRun, last_activity: datetime | None, now: datetime) -> bool:
    if run.status not in ACTIVE_STATUSES:
        return False
    if last_activity is None:
        # A live run always has at least its birth timestamp; a completely
        # clockless active run predates migration 0011 — treat as stalled so
        # it becomes recoverable rather than invisible.
        return True
    return (now - last_activity).total_seconds() > stall_budget_seconds(run)


def _eligible_applications(session: Session, run: ScreeningRun) -> list[Application]:
    """The same population run_start fans out over."""
    return list(
        session.scalars(
            select(Application).where(
                Application.job_id == run.job_id,
                Application.status.in_(["profiled", "screened", "shortlisted", "rejected"]),
            )
        )
    )


def _has_profile(session: Session, app: Application) -> bool:
    return (
        session.scalar(
            select(func.count())
            .select_from(CandidateProfileRow)
            .where(CandidateProfileRow.document_id == app.document_id)
        )
        > 0
    )


def recover(session: Session, run: ScreeningRun) -> list[str]:
    """Re-issue the messages a stranded run is missing. Idempotent: every
    re-issued task either guards on run status or skips finished work, so
    recovering a healthy run is a no-op with a paper trail."""
    from senthire.workers.tasks.screen import (
        _maybe_finish_deep_phase,
        _maybe_finish_light_phase,
        deep_application,
        finalize_run,
        poll_batch,
        run_start,
        score_run,
        screen_application,
    )

    if run.status not in ACTIVE_STATUSES:
        return []

    actions: list[str] = []
    enqueue: list[tuple] = []  # (task, args) — sent only after the audit commit

    if run.status == "queued":
        enqueue.append((run_start, (str(run.id),)))
        actions.append("run_start_reissued")

    elif run.status == "screening":
        batch = _batch_info(run)
        if batch:
            enqueue.append((poll_batch, (str(run.id), "light", batch["id"])))
            actions.append("light_batch_poll_reissued")
        else:
            evaluated = {
                row
                for row in session.scalars(
                    select(Evaluation.application_id).where(Evaluation.run_id == run.id)
                )
            }
            missing = [
                app
                for app in _eligible_applications(session, run)
                if app.id not in evaluated and _has_profile(session, app)
            ]
            if run.mode == "batch":
                # Batch mode but no submitted batch: the submit died mid-start.
                # Re-submitting would risk double spend; finish the stragglers
                # on the interactive lane instead — dearer, but the run lives.
                actions.append("batch_fallback_interactive")
            for app in missing:
                enqueue.append((screen_application, (str(run.id), str(app.id))))
            if missing:
                actions.append(f"screen_reissued:{len(missing)}")
            else:
                actions.append("light_phase_check")

    elif run.status == "selecting":
        enqueue.append((finalize_run, (str(run.id),)))
        actions.append("finalize_reissued")

    elif run.status == "deep_analysis":
        batch = _batch_info(run)
        if batch:
            enqueue.append((poll_batch, (str(run.id), "deep", batch["id"])))
            actions.append("deep_batch_poll_reissued")
        else:
            done = {
                str(row)
                for row in session.scalars(
                    select(Evaluation.application_id).where(
                        Evaluation.run_id == run.id, Evaluation.stage_reached == "deep"
                    )
                )
            }
            pending = [
                app_id
                for app_id in (run.funnel or {}).get("deep_pending", [])
                if app_id not in done
            ]
            if run.mode == "batch" and pending:
                actions.append("batch_fallback_interactive")
            for app_id in pending:
                enqueue.append((deep_application, (str(run.id), app_id)))
            if pending:
                actions.append(f"deep_reissued:{len(pending)}")
            else:
                actions.append("deep_phase_check")

    elif run.status == "scoring":
        enqueue.append((score_run, (str(run.id),)))
        actions.append("score_reissued")

    session.add(
        AuditLog(
            org_id=run.org_id,
            actor=None,
            event="run.recovered",
            entity={"type": "screening_run", "id": str(run.id)},
            detail={"status": run.status, "mode": run.mode, "actions": actions},
        )
    )
    session.commit()

    for task, args in enqueue:
        task.delay(*args)
    # Phase checks run after the enqueue commit so a fully-done phase advances
    # even when every worker message for it was lost.
    if "light_phase_check" in actions:
        _maybe_finish_light_phase(session, run)
    if "deep_phase_check" in actions:
        _maybe_finish_deep_phase(session, run)
    return actions
