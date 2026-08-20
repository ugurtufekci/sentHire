"""Golden-set file formats.

A *case* is one job (spec.json + expectations.json) plus a set of labeled
candidates (candidates/*.json). Labels have two distinct roles:

- ``labels.semantic`` — hand-labeled TRUE verdicts for every semantic/hybrid
  requirement. Offline they are fed into the pipeline in place of model output
  (so the deterministic stages are tested end-to-end on realistic data); in
  --live mode they are the answer key the model is graded against.
- ``labels.expected_*`` and the gate/band/score fields — assertions checked
  against what the pipeline actually computed. Any mismatch is a regression.

``expectations.as_of`` pins "today" for date math, so goldens do not drift as
real time passes.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SemanticLabel(_Strict):
    verdict: Literal["met", "partially_met", "not_met", "unknown"]
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    info_status: Literal["explicit", "inferred", "ambiguous", "missing"] = "explicit"


class GoldenLabels(_Strict):
    semantic: dict[str, SemanticLabel] = {}
    # Assertions (all optional except gate):
    expected_deterministic: dict[str, str] = {}  # req_id -> verdict after Stage 3
    expected_merged: dict[str, str] = {}  # req_id -> verdict after merge (hybrids)
    gate: Literal["pass", "fail"]
    knockout_reqs: list[str] = []  # asserted as a set when gate == "fail"
    borderline: bool = False  # Stage 3 borderline flag
    band: Literal["top", "strong", "possible", "weak"] | None = None
    score_range: tuple[float, float] | None = None
    needs_review: bool | None = None


class GoldenCandidate(_Strict):
    golden_id: str
    note: str | None = None
    # Marks a counterfactual twin of another candidate (fairness pairs assert
    # the two produce identical scores).
    variant_of: str | None = None
    profile: dict  # validated as ExtractedProfile at load time
    labels: GoldenLabels


class CaseExpectations(_Strict):
    # Why this case asserts what it asserts — read by people, not by code.
    note: str | None = None
    as_of: date
    top_k: int = 3
    # golden_ids expected inside the top_k (set membership, order-free)
    expected_top: list[str] = []
    # [higher, lower] — higher must outrank lower among gate-pass candidates
    expected_order_pairs: list[tuple[str, str]] = []
    # [a, b] — must produce identical final scores and bands
    fairness_pairs: list[tuple[str, str]] = []
    # [higher_or_equal, lower_or_equal] — a *weak* ordering, unlike
    # expected_order_pairs. Generated from invariant twins (one more year of
    # experience may never lower a score; a weaker language level may never
    # raise one), so the assertion holds by construction rather than by
    # anyone's judgment.
    monotonic_pairs: list[tuple[str, str]] = []
