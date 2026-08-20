"""Stage 2 — compile template + HR natural language into a draft EvaluationSpec.

The LLM (Sonnet tier) proposes; code validates and assembles. Everything the
model emits is checked against the predicate registry — invalid deterministic
checks are downgraded to semantic requirements with a warning, never trusted
and never dropped (docs/04 §4).
"""

import json
from dataclasses import dataclass, field

import anthropic

from senthire.compiler import prompts
from senthire.compiler.schema import CompilerOutput, DraftRequirement
from senthire.config import get_settings
from senthire.domain.predicates import PredicateError, evaluate
from senthire.domain.spec import (
    Clarification,
    DeterministicCheck,
    EvaluationSpec,
    Requirement,
    RequirementSource,
    RubricAnchor,
    SemanticCheck,
)

_EMPTY_PROFILE: dict = {"derived": {}, "location": {}, "languages": [], "industries": [],
                        "tools_technologies": [], "skills": [], "certifications": [],
                        "experience": []}


class CompilationFailed(RuntimeError):
    pass


@dataclass
class CompileResult:
    spec: EvaluationSpec
    back_translation: dict[str, str]
    clarifications: list[dict]
    compliance_flags: list[dict]
    warnings: list[str] = field(default_factory=list)
    usage: dict | None = None


def compile_spec(
    template_spec: EvaluationSpec | None,
    nl_text: str,
    *,
    version: int,
    locale: str = "tr",
) -> CompileResult:
    if get_settings().fake_models:
        from senthire.demo.models import compile_spec as offline

        return offline(template_spec, nl_text, version=version, locale=locale)

    """LLM call + deterministic assembly. Runs in a worker (docs/01 §3)."""
    settings = get_settings()
    template_reqs = list(template_spec.requirements) if template_spec else []
    template_json = json.dumps(
        [r.model_dump(exclude_none=True) for r in template_reqs], ensure_ascii=False, indent=1
    )

    try:
        response = anthropic.Anthropic().messages.parse(
            model=settings.compiler_model,
            max_tokens=8192,
            system=prompts.COMPILER_SYSTEM.format(registry=prompts.REGISTRY_DOC),
            messages=[
                {
                    "role": "user",
                    "content": prompts.COMPILER_USER.format(
                        template_json=template_json, nl_text=nl_text.strip()
                    ),
                }
            ],
            output_format=CompilerOutput,
        )
    except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError):
        raise  # transient — task layer retries
    except anthropic.APIStatusError as exc:
        raise CompilationFailed(f"compiler call failed: {exc.status_code} {exc.message}") from exc

    output = response.parsed_output
    if output is None:
        raise CompilationFailed("compiler returned no parseable output")

    spec, warnings = assemble_spec(template_spec, output, version=version, locale=locale)
    return CompileResult(
        spec=spec,
        back_translation={"tr": output.back_translation_tr, "en": output.back_translation_en},
        clarifications=[
            {
                "req_id": r.req_id,
                "question": r.clarification_question,
                "default": r.clarification_default,
            }
            for r in output.requirements
            if r.clarification_question
        ],
        compliance_flags=[f.model_dump() for f in output.compliance_flags],
        warnings=warnings + list(output.warnings),
        usage={
            "model": settings.compiler_model,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
    )


def assemble_spec(
    template_spec: EvaluationSpec | None,
    output: CompilerOutput,
    *,
    version: int,
    locale: str,
) -> tuple[EvaluationSpec, list[str]]:
    """Pure: merge template + drafted requirements into a validated draft spec."""
    warnings: list[str] = []
    absorbed = {rid for r in output.requirements for rid in r.absorbs_template_req_ids}

    requirements: list[Requirement] = []
    if template_spec:
        for req in template_spec.requirements:
            if req.req_id in absorbed:
                continue
            requirements.append(req)

    seen_ids = {r.req_id for r in requirements}
    for draft in output.requirements:
        req, req_warnings = convert_requirement(draft)
        warnings.extend(req_warnings)
        if req.req_id in seen_ids:
            base = req.req_id
            n = 2
            while f"{base}_{n}" in seen_ids:
                n += 1
            warnings.append(f"duplicate req_id {base} renamed to {base}_{n}")
            req = req.model_copy(update={"req_id": f"{base}_{n}"})
        seen_ids.add(req.req_id)
        requirements.append(req)

    weights = dict(template_spec.weights) if template_spec else None
    spec = EvaluationSpec(
        version=version,
        locale=locale,
        requirements=requirements,
        **({"weights": weights} if weights else {}),
        compliance={
            "lint_passed": not any(f.action == "blocked" for f in output.compliance_flags),
            "flags": [f.model_dump() for f in output.compliance_flags],
        },
    )
    return spec, warnings


def convert_requirement(draft: DraftRequirement) -> tuple[Requirement, list[str]]:
    """Pure: DraftRequirement → validated Requirement (with safe downgrades)."""
    warnings: list[str] = []
    evaluator = draft.evaluator
    deterministic: DeterministicCheck | None = None

    if evaluator in {"deterministic", "hybrid"}:
        predicate = build_predicate(draft)
        if predicate is None:
            warnings.append(
                f"{draft.req_id}: no valid deterministic conditions — downgraded to semantic"
            )
            evaluator = "semantic"
        else:
            try:
                evaluate(predicate, _EMPTY_PROFILE)  # registry/type check, result irrelevant
                deterministic = DeterministicCheck(
                    predicate=predicate,
                    borderline_tolerance=draft.borderline_tolerance,
                    penalty_points=draft.penalty_points,
                )
            except PredicateError as exc:
                warnings.append(f"{draft.req_id}: invalid predicate ({exc}) — downgraded to semantic")
                evaluator = "semantic"

    semantic: SemanticCheck | None = None
    if evaluator in {"semantic", "hybrid"}:
        rubric = draft.rubric or f"Judge whether the candidate satisfies: {draft.label_en}. Cite evidence."
        if not draft.rubric:
            warnings.append(f"{draft.req_id}: missing rubric — generic rubric substituted")
        anchors = [
            RubricAnchor(
                score=a.score, label={"tr": a.label_tr}, definition=a.definition
            )
            for a in draft.anchors
        ]
        if anchors and len(anchors) < 2:
            # A one-rung ladder cannot separate anybody; the default is better
            # than a scale with a single value on it.
            warnings.append(f"{draft.req_id}: fewer than two anchors — default scale used")
            anchors = []
        semantic = SemanticCheck(rubric=rubric, anchors=anchors)

    if evaluator == "semantic" and deterministic is None and draft.type == "penalty":
        # penalties need a checkable trigger or an explicit semantic rubric — keep semantic
        pass

    clarification = None
    if draft.clarification_question:
        clarification = Clarification(
            question=draft.clarification_question, default=draft.clarification_default
        )

    req = Requirement(
        req_id=draft.req_id,
        category=draft.category,
        label={"tr": draft.label_tr, "en": draft.label_en},
        type=draft.type,
        importance=draft.importance,
        evaluator=evaluator,
        deterministic=deterministic,
        semantic=semantic,
        missing_policy=draft.missing_policy,
        bonus_points=draft.bonus_points,
        clarification=clarification,
        source=RequirementSource(kind="hr_text", original=draft.source_sentence),
    )
    return req, warnings


def build_predicate(draft: DraftRequirement) -> dict | None:
    leaves = []
    for cond in draft.conditions:
        value = cond.value()
        leaf: dict = {"field": cond.field, "op": cond.op}
        if cond.op != "exists":
            if value is None:
                continue
            leaf["value"] = value
        leaves.append(leaf)
    if not leaves:
        return None
    if len(leaves) == 1:
        return leaves[0]
    return {draft.combine: leaves}
