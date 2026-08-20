"""Golden-set CLI.

    python -m senthire.evals                     # offline: must be 100% clean
    python -m senthire.evals --case NAME         # one case only
    python -m senthire.evals --live              # also grade the real model
    python -m senthire.evals --json report.json  # machine-readable report

Offline exits non-zero on any mismatch. Live mode exits non-zero when model
agreement drops below --min-agreement (default 0.85).
"""

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from senthire.evals.loader import load_cases
from senthire.evals.runner import run_case, run_case_live

DEFAULT_ROOT = Path("goldens/cases")


def repin(cases, root: Path) -> int:
    """Re-record the pinned scores after an intentional scoring change.

    Pins are regression guards, not truth (docs/13 §6): they exist so that an
    unintended movement fails the build. When the movement *is* intended, the
    honest workflow is to re-pin and read the diff — which is what this prints,
    old value beside new, so the change gets looked at rather than accepted.
    """
    from senthire.evals.runner import run_case

    changed = 0
    for case in cases:
        report = run_case(case)
        outcomes = {o.golden_id: o for o in report.outcomes}
        for candidate in case.candidates:
            outcome = outcomes[candidate.golden_id]
            path = root / case.name / "candidates" / f"{candidate.golden_id}.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            labels = payload.get("labels", {})
            before = (labels.get("band"), labels.get("score_range"))
            after = (outcome.result.band, [outcome.result.final_score, outcome.result.final_score])
            if before[0] is None and before[1] is None:
                continue  # this candidate was never pinned; leave it unpinned
            if before[0] == after[0] and before[1] == after[1]:
                continue
            labels["band"], labels["score_range"] = after
            payload["labels"] = labels
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            changed += 1
            print(
                f"  {case.name}/{candidate.golden_id}: "
                f"{before[0]} {before[1]} → {after[0]} {after[1]}"
            )
    print(f"\nre-pinned {changed} candidate(s). Read the diff before committing it.")
    return 0

OK = "✓"
BAD = "✗"


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m senthire.evals")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--case", default=None, help="run a single case by name")
    parser.add_argument("--live", action="store_true", help="also grade the real model")
    parser.add_argument("--min-agreement", type=float, default=0.85)
    parser.add_argument("--json", type=Path, default=None, help="write a JSON report")
    parser.add_argument(
        "--repin",
        action="store_true",
        help="rewrite pinned band/score_range from what the pipeline computes now",
    )
    args = parser.parse_args()

    if args.repin:
        return repin(load_cases(args.root, only=args.case), args.root)

    cases = load_cases(args.root, only=args.case)
    failed = False
    json_report: dict = {"offline": [], "live": []}

    for case in cases:
        report = run_case(case)
        print(f"case {case.name} — {len(report.outcomes)} candidates, "
              f"as of {case.expectations.as_of}")
        for outcome in report.outcomes:
            mark = OK if outcome.ok else BAD
            print(f"  {mark} {outcome.golden_id:<28} gate={outcome.gate:<4} "
                  f"band={outcome.result.band:<8} score={outcome.result.final_score}")
            for mismatch in outcome.mismatches:
                print(f"      - {mismatch}")
        top = report.ranking[: case.expectations.top_k]
        print(f"  ranking (gate-pass): {report.ranking}")
        for mismatch in report.case_mismatches:
            print(f"  {BAD} {mismatch}")
        if report.mismatch_count == 0:
            print(f"  {OK} clean (top-{case.expectations.top_k}: {top})")
        else:
            failed = True
        json_report["offline"].append(
            {
                "case": case.name,
                "mismatches": report.mismatch_count,
                "ranking": report.ranking,
                "candidates": [
                    {
                        "golden_id": o.golden_id,
                        "gate": o.gate,
                        "band": o.result.band,
                        "score": o.result.final_score,
                        "mismatches": o.mismatches,
                    }
                    for o in report.outcomes
                ],
            }
        )
        print()

    if args.live:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("--live needs ANTHROPIC_API_KEY in the environment", file=sys.stderr)
            return 2
        for case in cases:
            live = run_case_live(case)
            agreement = live.agreement
            adjacent = sum(r.adjacent for r in live.rows)
            print(f"live {case.name} — {len(live.rows)} labeled verdicts")
            for row in live.rows:
                if not row.exact:
                    near = " (adjacent)" if row.adjacent else ""
                    print(f"  {BAD} {row.golden_id} {row.req_id}: "
                          f"expected {row.expected}, got {row.got}{near}")
            for error in live.errors:
                print(f"  {BAD} error: {error}")
            if agreement is not None:
                print(f"  agreement: {agreement:.1%} exact "
                      f"({adjacent} adjacent misses) — threshold {args.min_agreement:.0%}")
                print(f"  tokens: in={live.input_tokens} out={live.output_tokens} "
                      f"cache_read={live.cache_read_tokens}")
                if agreement < args.min_agreement or live.errors:
                    failed = True
            else:
                print("  no rows graded")
                failed = True
            json_report["live"].append(
                {
                    "case": case.name,
                    "agreement": agreement,
                    "errors": live.errors,
                    "rows": [asdict(r) for r in live.rows],
                    "tokens": {
                        "input": live.input_tokens,
                        "output": live.output_tokens,
                        "cache_read": live.cache_read_tokens,
                    },
                }
            )
            print()

    if args.json:
        args.json.write_text(json.dumps(json_report, indent=2, ensure_ascii=False))
        print(f"report written to {args.json}")

    print("RESULT: " + ("FAIL" if failed else "PASS"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
