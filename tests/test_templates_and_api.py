import json
from importlib import resources

from fastapi.testclient import TestClient

from senthire.api.app import create_app
from senthire.domain.predicates import evaluate
from senthire.domain.spec import EvaluationSpec

MINIMAL_PROFILE = {"derived": {}, "location": {}, "languages": [], "industries": [],
                   "tools_technologies": [], "skills": [], "certifications": [], "experience": []}


def iter_seed_templates():
    root = resources.files("senthire") / "templates_seed"
    for entry in root.iterdir():
        if entry.name.endswith(".json"):
            yield json.loads(entry.read_text(encoding="utf-8"))


def test_seed_templates_are_valid_specs():
    templates = list(iter_seed_templates())
    assert templates, "no seed templates found"
    for data in templates:
        spec = EvaluationSpec.model_validate(data["spec_seed"])
        assert spec.requirements
        assert abs(sum(spec.weights.values()) - 1.0) < 1e-6


def test_seed_template_predicates_use_registry_fields_only():
    for data in iter_seed_templates():
        spec = EvaluationSpec.model_validate(data["spec_seed"])
        for req in spec.requirements:
            if req.deterministic:
                # must not raise PredicateError; unknown result is fine on an empty profile
                result = evaluate(req.deterministic.predicate, MINIMAL_PROFILE)
                assert result in {"pass", "fail", "unknown"}


def test_health_endpoint_no_auth_required():
    client = TestClient(create_app())
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_endpoints_reject_missing_api_key():
    client = TestClient(create_app())
    assert client.get("/api/v1/jobs").status_code == 401
