"""The shipped golden set must stay 100% clean — this makes it part of CI.

Any scorer, predicate, merge, or derived-field change that shifts a golden
outcome fails here with the exact candidate and field that moved.
"""

from pathlib import Path

import pytest

from senthire.evals.loader import GoldenLoadError, load_cases
from senthire.evals.runner import run_case

GOLDEN_ROOT = Path(__file__).resolve().parent.parent / "goldens" / "cases"


def test_golden_cases_load_and_validate():
    cases = load_cases(GOLDEN_ROOT)
    assert cases, "no golden cases found"
    for case in cases:
        assert case.candidates


def test_golden_cases_run_clean():
    for case in load_cases(GOLDEN_ROOT):
        report = run_case(case)
        problems = [
            f"{o.golden_id}: {m}" for o in report.outcomes for m in o.mismatches
        ] + report.case_mismatches
        assert not problems, f"case {case.name}:\n" + "\n".join(problems)


def test_loader_rejects_unlabeled_semantic_requirements(tmp_path):
    import json
    import shutil

    source = GOLDEN_ROOT / "b2b-sales-ankara"
    case_dir = tmp_path / "broken-case"
    shutil.copytree(source, case_dir)
    candidate_path = next((case_dir / "candidates").glob("*.json"))
    data = json.loads(candidate_path.read_text(encoding="utf-8"))
    data["labels"]["semantic"].pop("R2_b2b_sales_3y")
    candidate_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(GoldenLoadError, match="semantic labels missing"):
        load_cases(tmp_path)
