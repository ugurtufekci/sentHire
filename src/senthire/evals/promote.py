"""Corpus + labels + invariants → a golden case the CI gate runs on every push.

Promotion is where automation has to be conservative, because everything it
writes becomes a rule that can fail a build. Three kinds of assertion come out
of it, and they are not equally trustworthy — so they are kept separate:

- **True by construction** — knockout twins must be gated out by the rule they
  violate; fairness twins must score identically; a year of extra experience
  may not lower a score. No model or human is trusted for these.
- **True if the labels are** — per-requirement verdicts, and the gate that
  follows from them. The ensemble's agreement rate rides along, so a weakly
  agreed label can be excluded by raising --min-confidence.
- **Pins, not truth** — band and exact score, written only with --pin-scores.
  They catch unintended movement; when a change to the scorer is intentional,
  you re-promote and review the diff.

A case that cannot be labeled cleanly is skipped with a stated reason, never
promoted with a guess.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from senthire.domain.scoring import RequirementVerdict
from senthire.evals.corpus import AutoLabel, Pool, write_json
from senthire.evals.invariants import Twin, generate
from senthire.evals.runner import evaluate_profile


@dataclass
class PromotionReport:
    case_dir: Path
    promoted: list[str] = field(default_factory=list)
    skipped: dict[str, list[str]] = field(default_factory=dict)
    twins: Counter = field(default_factory=Counter)
    rejected_twins: list[str] = field(default_factory=list)

    def skip(self, reason: str, corpus_id: str) -> None:
        self.skipped.setdefault(reason, []).append(corpus_id)


def _verdicts(labels: dict[str, AutoLabel]) -> dict[str, RequirementVerdict]:
    return {
        req_id: RequirementVerdict(
            req_id=req_id,
            verdict=label.verdict,
            score=label.score,
            confidence=label.confidence,
            info_status=label.info_status,
            source_stage="light",
        )
        for req_id, label in labels.items()
    }


def _semantic_block(labels: dict[str, AutoLabel], semantic_ids: set[str]) -> dict:
    return {
        req_id: {
            "verdict": label.verdict,
            "score": label.score,
            "confidence": label.confidence,
            "info_status": label.info_status,
        }
        for req_id, label in labels.items()
        if req_id in semantic_ids
    }


def _candidate_payload(
    golden_id: str,
    profile: dict,
    semantic: dict,
    outcome,
    *,
    note: str | None = None,
    variant_of: str | None = None,
    pin_scores: bool = False,
    knockout_reqs: list[str] | None = None,
) -> dict:
    labels: dict = {
        "semantic": semantic,
        "gate": outcome.gate,
        "borderline": outcome.borderline,
    }
    if outcome.gate == "fail":
        labels["knockout_reqs"] = knockout_reqs or outcome.knockouts
    if pin_scores:
        labels["band"] = outcome.result.band
        labels["score_range"] = [outcome.result.final_score, outcome.result.final_score]
    payload = {"golden_id": golden_id, "profile": profile, "labels": labels}
    if note:
        payload["note"] = note
    if variant_of:
        payload["variant_of"] = variant_of
    return payload


def promote(
    pool: Pool,
    job: str,
    *,
    out_root: Path,
    case_name: str,
    salt: str,
    as_of: date,
    min_confidence: float = 0.8,
    top_k: int = 3,
    pin_scores: bool = False,
    with_invariants: bool = True,
    order_pairs: list[dict] | None = None,
) -> PromotionReport:
    spec = pool.spec(job)
    label_set = pool.labels(job)
    if label_set is None:
        raise ValueError(f"pool '{pool.name}' job '{job}' has no labels — run `label` first")

    semantic_ids = {r.req_id for r in spec.requirements if r.evaluator in {"semantic", "hybrid"}}
    case_dir = out_root / case_name
    report = PromotionReport(case_dir=case_dir)
    candidates: list[dict] = []
    fairness_pairs: list[list[str]] = []
    monotonic_pairs: list[list[str]] = []

    for case in pool.cases():
        labels = label_set.cases.get(case.corpus_id)
        if not labels:
            report.skip("unlabeled", case.corpus_id)
            continue
        missing = semantic_ids - set(labels)
        if missing:
            report.skip("incomplete_labels", case.corpus_id)
            continue
        if any(label.needs_adjudication for label in labels.values()):
            report.skip("awaiting_adjudication", case.corpus_id)
            continue
        weak = [
            req_id
            for req_id, label in labels.items()
            if req_id in semantic_ids and label.confidence < min_confidence
        ]
        if weak:
            report.skip("low_confidence", case.corpus_id)
            continue

        semantic = _semantic_block(labels, semantic_ids)
        outcome = evaluate_profile(spec, case.profile, _verdicts(labels), as_of)
        candidates.append(
            _candidate_payload(
                case.corpus_id, case.profile, semantic, outcome,
                note=f"corpus:{pool.name} ({case.identity_class} name coding)",
                pin_scores=pin_scores,
            )
        )
        report.promoted.append(case.corpus_id)

        if not with_invariants:
            continue
        for twin in generate(case, spec, salt=salt):
            payload = _twin_payload(
                spec, twin, semantic, as_of, pin_scores=pin_scores, report=report
            )
            if payload is None:
                continue
            candidates.append(payload)
            report.twins[twin.kind] += 1
            if twin.assertion == "equal":
                fairness_pairs.append([twin.base_id, twin.twin_id])
            elif twin.assertion == "not_lower":
                monotonic_pairs.append([twin.twin_id, twin.base_id])
            elif twin.assertion == "not_higher":
                monotonic_pairs.append([twin.base_id, twin.twin_id])

    if not candidates:
        return report  # nothing written; the report says why

    promoted_ids = {c["golden_id"] for c in candidates}
    pairs = [
        [p["higher"], p["lower"]]
        for p in (order_pairs or [])
        if p["higher"] in promoted_ids and p["lower"] in promoted_ids
    ]
    expectations = {
        "as_of": as_of.isoformat(),
        "top_k": top_k,
        "expected_order_pairs": pairs,
        "fairness_pairs": fairness_pairs,
        "monotonic_pairs": monotonic_pairs,
    }

    write_json(case_dir / "spec.json", spec.model_dump(mode="json"))
    write_json(case_dir / "expectations.json", expectations)
    for payload in candidates:
        write_json(case_dir / "candidates" / f"{payload['golden_id']}.json", payload)
    return report


def _twin_payload(
    spec, twin: Twin, semantic: dict, as_of: date, *, pin_scores: bool, report: PromotionReport
) -> dict | None:
    """Build a twin candidate, verifying the invariant is actually constructed.

    A knockout twin that does not get knocked out means the edit failed to
    violate the rule (or the engine is wrong). Either way, emitting it would
    bake a falsehood into the suite, so it is reported and dropped.
    """
    outcome = evaluate_profile(spec, twin.profile, _verdicts_from_semantic(semantic), as_of)
    if twin.assertion == "gate_fail":
        expected_req = twin.detail.get("req_id")
        if outcome.gate != "fail" or expected_req not in outcome.knockouts:
            report.rejected_twins.append(
                f"{twin.twin_id}: expected knockout on {expected_req}, "
                f"got gate={outcome.gate} knockouts={outcome.knockouts}"
            )
            return None
    return _candidate_payload(
        twin.twin_id,
        twin.profile,
        semantic,
        outcome,
        note=f"invariant twin ({twin.kind}): {twin.detail}",
        variant_of=twin.base_id,
        pin_scores=pin_scores,
        knockout_reqs=twin.knockout_reqs or None,
    )


def _verdicts_from_semantic(semantic: dict) -> dict[str, RequirementVerdict]:
    return {
        req_id: RequirementVerdict(
            req_id=req_id,
            verdict=label["verdict"],
            score=label.get("score"),
            confidence=label.get("confidence", 1.0),
            info_status=label.get("info_status", "explicit"),
            source_stage="light",
        )
        for req_id, label in semantic.items()
    }
