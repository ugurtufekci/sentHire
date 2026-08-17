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

OK = "✓"
BAD = "✗"


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m senthire.evals")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--case", default=None, help="run a single case by name")
    parser.add_argument("--live", action="store_true", help="also grade the real model")
    parser.add_argument("--min-agreement", type=float, default=0.85)
    parser.add_argument("--json", type=Path, default=None, help="write a JSON report")
    args = parser.parse_args()

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
