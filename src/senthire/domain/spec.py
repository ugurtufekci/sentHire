"""Evaluation Spec schema (docs/03 §4) — the compiled, versioned job requirements."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SPEC_SCHEMA_VERSION = "1.0"

RequirementType = Literal["hard", "scored", "bonus", "penalty", "disqualifier", "info"]
Importance = Literal["critical", "high", "medium", "low"]
Evaluator = Literal["deterministic", "semantic", "hybrid"]
MissingPolicy = Literal["unknown", "fail", "ignore"]

DEFAULT_WEIGHTS: dict[str, float] = {
    "relevant_experience": 0.25,
    "skills": 0.20,
    "industry": 0.15,
    "career_stability": 0.10,
    "education": 0.10,
    "language": 0.05,
    "location": 0.05,
    "custom": 0.10,
}

DEFAULT_BONUS_CAP = 10.0


class DeterministicCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    predicate: dict
    borderline_tolerance: float | None = None  # fraction, e.g. 0.1 (docs/02 Stage 3)
    penalty_points: float | None = None  # only for type=penalty


class RubricAnchor(BaseModel):
    """One rung of a requirement's scale, with the evidence that earns it."""

    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0.0, le=1.0)
    label: dict[str, str] = {}
    definition: str


class SemanticCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rubric: str
    # The ladder this requirement is judged on. Empty means the default ladder
    # (senthire.domain.anchors) — every semantic requirement is anchored, so
    # comparability never depends on the compiler having been thorough.
    anchors: list[RubricAnchor] = []
    target_field: str | None = None


class Clarification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    default: str | None = None
    hr_answered: bool = False


class RequirementSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["template", "hr_text", "hr_edit", "inferred"]
    original: str | None = None


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    req_id: str
    category: str
    label: dict[str, str] = {}
    type: RequirementType
    importance: Importance = "medium"
    evaluator: Evaluator
    deterministic: DeterministicCheck | None = None
    semantic: SemanticCheck | None = None
    missing_policy: MissingPolicy = "unknown"
    weight_within_category: float = 1.0
    bonus_points: float | None = None
    clarification: Clarification | None = None
    source: RequirementSource | None = None

    def display_label(self, locale: str = "en") -> str:
        return self.label.get(locale) or next(iter(self.label.values()), self.req_id)


class EvaluationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SPEC_SCHEMA_VERSION
    version: int = 1
    locale: str = "tr"
    weights: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    bonus_cap: float = DEFAULT_BONUS_CAP
    requirements: list[Requirement] = []
    compliance: dict | None = None
    compiler: dict | None = None

    def hard_requirements(self) -> list[Requirement]:
        return [r for r in self.requirements if r.type == "hard"]

    def by_id(self, req_id: str) -> Requirement | None:
        return next((r for r in self.requirements if r.req_id == req_id), None)
