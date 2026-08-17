from senthire.compiler.compiler import assemble_spec, build_predicate, convert_requirement
from senthire.compiler.schema import (
    CompilerOutput,
    ComplianceFlag,
    DraftCondition,
    DraftRequirement,
)
from senthire.domain.spec import EvaluationSpec, Requirement, SemanticCheck


def draft(**overrides) -> DraftRequirement:
    base = dict(
        req_id="R1_b2b_sales_3y",
        category="relevant_experience",
        label_tr="En az 3 yıl B2B satış deneyimi",
        label_en="≥3 years B2B sales experience",
        type="hard",
        importance="critical",
        evaluator="hybrid",
        conditions=[
            DraftCondition(field="derived.total_experience_months", op=">=", value_number=36)
        ],
        borderline_tolerance=0.1,
        rubric="Count only B2B sales roles; cite the roles counted.",
        source_sentence="En az 3 yıl B2B satış deneyimi olsun.",
    )
    base.update(overrides)
    return DraftRequirement(**base)


def test_convert_hybrid_requirement():
    req, warnings = convert_requirement(draft())
    assert warnings == []
    assert req.evaluator == "hybrid"
    assert req.deterministic.predicate == {
        "field": "derived.total_experience_months", "op": ">=", "value": 36.0
    }
    assert req.deterministic.borderline_tolerance == 0.1
    assert "B2B" in req.semantic.rubric
    assert req.source.original == "En az 3 yıl B2B satış deneyimi olsun."


def test_invalid_field_downgrades_to_semantic():
    bad = draft(
        evaluator="deterministic",
        conditions=[DraftCondition(field="identity.full_name", op="==", value_text="x")],
        rubric=None,
    )
    req, warnings = convert_requirement(bad)
    assert req.evaluator == "semantic"
    assert req.deterministic is None
    assert req.semantic is not None  # generic rubric substituted
    assert any("downgraded" in w for w in warnings)


def test_no_conditions_downgrades():
    req, warnings = convert_requirement(draft(evaluator="deterministic", conditions=[], rubric=None))
    assert req.evaluator == "semantic"
    assert any("downgraded" in w for w in warnings)


def test_build_predicate_combines_leaves():
    d = draft(
        conditions=[
            DraftCondition(field="derived.total_experience_months", op=">=", value_number=36),
            DraftCondition(field="location.city_canonical", op="==", value_text="Ankara"),
        ],
        combine="all",
    )
    assert build_predicate(d) == {
        "all": [
            {"field": "derived.total_experience_months", "op": ">=", "value": 36.0},
            {"field": "location.city_canonical", "op": "==", "value": "Ankara"},
        ]
    }


def _template() -> EvaluationSpec:
    return EvaluationSpec(
        requirements=[
            Requirement(
                req_id="T2_sales_experience",
                category="relevant_experience",
                type="scored",
                evaluator="semantic",
                semantic=SemanticCheck(rubric="generic sales rubric"),
            )
        ]
    )


def test_assemble_absorbs_template_and_flags_compliance():
    output = CompilerOutput(
        requirements=[draft(absorbs_template_req_ids=["T2_sales_experience"])],
        compliance_flags=[
            ComplianceFlag(
                original_text="30 yaş altı olsun",
                issue="age-based criterion",
                action="blocked",
            )
        ],
        back_translation_tr="Anladığımız: …",
        back_translation_en="Our understanding: …",
    )
    spec, _ = assemble_spec(_template(), output, version=2, locale="tr")
    ids = [r.req_id for r in spec.requirements]
    assert "T2_sales_experience" not in ids  # absorbed
    assert "R1_b2b_sales_3y" in ids
    assert spec.version == 2
    assert spec.compliance["lint_passed"] is False
    assert spec.compliance["flags"][0]["action"] == "blocked"


def test_assemble_renames_duplicate_ids():
    output = CompilerOutput(
        requirements=[draft(), draft()],
        back_translation_tr="t", back_translation_en="e",
    )
    spec, warnings = assemble_spec(None, output, version=1, locale="tr")
    ids = [r.req_id for r in spec.requirements]
    assert len(ids) == len(set(ids)) == 2
    assert any("renamed" in w for w in warnings)
