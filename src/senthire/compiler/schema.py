"""What the compiler LLM emits (structured outputs — flat, no open dicts).

The model never writes the predicate DSL directly: it emits flat leaf
conditions + a combinator, and code builds/validates the predicate against the
registry (docs/04 §4). Anything that doesn't validate is downgraded to a
semantic requirement, never silently dropped.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from senthire.domain.spec import Evaluator, Importance, MissingPolicy, RequirementType

CATEGORIES = (
    "relevant_experience",
    "skills",
    "industry",
    "career_stability",
    "education",
    "language",
    "location",
    "custom",
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftCondition(_Strict):
    field: str  # must be a registry path, e.g. "derived.total_experience_months"
    op: Literal["==", "!=", ">", ">=", "<", "<=", "in", "not_in", "contains", "exists"]
    value_number: float | None = None
    value_text: str | None = None
    value_list: list[str] | None = None

    def value(self):
        if self.value_number is not None:
            return self.value_number
        if self.value_list is not None:
            return self.value_list
        return self.value_text


class DraftAnchor(_Strict):
    score: float  # 0..1
    label_tr: str
    definition: str  # what a CV must show to land on this rung


class DraftRequirement(_Strict):
    req_id: str  # short snake_case slug, e.g. "R1_b2b_sales_3y"
    category: Literal[
        "relevant_experience",
        "skills",
        "industry",
        "career_stability",
        "education",
        "language",
        "location",
        "custom",
    ]
    label_tr: str
    label_en: str
    type: RequirementType
    importance: Importance
    evaluator: Evaluator
    conditions: list[DraftCondition] = []
    combine: Literal["all", "any"] = "all"
    borderline_tolerance: float | None = None
    penalty_points: float | None = None
    bonus_points: float | None = None
    missing_policy: MissingPolicy = "unknown"
    rubric: str | None = None  # required for semantic/hybrid
    # The rungs this requirement is judged on. 3–5 of them, highest first.
    # Omitted → the default ladder (senthire.domain.anchors).
    anchors: list[DraftAnchor] = []
    clarification_question: str | None = None
    clarification_default: str | None = None
    absorbs_template_req_ids: list[str] = []
    source_sentence: str | None = None  # HR's original sentence, verbatim


class ComplianceFlag(_Strict):
    original_text: str
    issue: str  # why this criterion is not allowed / risky
    action: Literal["blocked", "rewritten"]
    rewritten_to: str | None = None


class CompilerOutput(_Strict):
    requirements: list[DraftRequirement]
    compliance_flags: list[ComplianceFlag] = []
    back_translation_tr: str  # "Anladığımız: …" shown beside HR's original text
    back_translation_en: str
    warnings: list[str] = []
