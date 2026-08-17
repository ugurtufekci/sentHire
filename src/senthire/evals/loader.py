"""Discover and validate golden cases from a directory tree.

Layout:
    goldens/cases/<case-name>/
        spec.json           EvaluationSpec document
        expectations.json   CaseExpectations
        candidates/*.json   GoldenCandidate files
"""

import json
from dataclasses import dataclass
from pathlib import Path

from senthire.domain.profile import ExtractedProfile
from senthire.domain.spec import EvaluationSpec
from senthire.evals.schema import CaseExpectations, GoldenCandidate


class GoldenLoadError(ValueError):
    pass


@dataclass
class GoldenCase:
    name: str
    spec: EvaluationSpec
    expectations: CaseExpectations
    candidates: list[GoldenCandidate]


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GoldenLoadError(f"{path}: invalid JSON — {exc}") from exc


def load_case(case_dir: Path) -> GoldenCase:
    spec = EvaluationSpec.model_validate(_read_json(case_dir / "spec.json"))
    expectations = CaseExpectations.model_validate(
        _read_json(case_dir / "expectations.json")
    )
    candidates = [
        GoldenCandidate.model_validate(_read_json(p))
        for p in sorted((case_dir / "candidates").glob("*.json"))
    ]
    if not candidates:
        raise GoldenLoadError(f"{case_dir}: no candidates")

    case = GoldenCase(case_dir.name, spec, expectations, candidates)
    _validate(case, case_dir)
    return case


def _validate(case: GoldenCase, case_dir: Path) -> None:
    req_ids = {r.req_id for r in case.spec.requirements}
    semantic_required = {
        r.req_id for r in case.spec.requirements if r.evaluator in {"semantic", "hybrid"}
    }
    ids = [c.golden_id for c in case.candidates]
    if len(ids) != len(set(ids)):
        raise GoldenLoadError(f"{case_dir}: duplicate golden_ids")
    known = set(ids)

    for cand in case.candidates:
        where = f"{case_dir}/candidates/{cand.golden_id}"
        ExtractedProfile.model_validate(cand.profile)  # raises on schema drift
        for bucket_name, bucket in (
            ("semantic", cand.labels.semantic),
            ("expected_deterministic", cand.labels.expected_deterministic),
            ("expected_merged", cand.labels.expected_merged),
        ):
            unknown_reqs = set(bucket) - req_ids
            if unknown_reqs:
                raise GoldenLoadError(
                    f"{where}: {bucket_name} references unknown req_ids {sorted(unknown_reqs)}"
                )
        missing = semantic_required - set(cand.labels.semantic)
        if missing:
            raise GoldenLoadError(
                f"{where}: semantic labels missing for {sorted(missing)} — every "
                "semantic/hybrid requirement needs a labeled true verdict"
            )
        if cand.variant_of is not None and cand.variant_of not in known:
            raise GoldenLoadError(f"{where}: variant_of '{cand.variant_of}' not found")

    for name, listed in (
        ("expected_top", case.expectations.expected_top),
        ("expected_order_pairs", [g for pair in case.expectations.expected_order_pairs for g in pair]),
        ("fairness_pairs", [g for pair in case.expectations.fairness_pairs for g in pair]),
    ):
        unknown_ids = set(listed) - known
        if unknown_ids:
            raise GoldenLoadError(
                f"{case_dir}: {name} references unknown golden_ids {sorted(unknown_ids)}"
            )


def load_cases(root: Path, only: str | None = None) -> list[GoldenCase]:
    if not root.is_dir():
        raise GoldenLoadError(f"golden root not found: {root}")
    dirs = sorted(d for d in root.iterdir() if d.is_dir())
    if only is not None:
        dirs = [d for d in dirs if d.name == only]
        if not dirs:
            raise GoldenLoadError(f"case '{only}' not found under {root}")
    return [load_case(d) for d in dirs]
