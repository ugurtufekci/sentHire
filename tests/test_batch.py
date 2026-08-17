"""Batch transport: schema sanitizing, request shape, result parsing, pricing."""

import json

from senthire.domain.spec import EvaluationSpec
from senthire.screening import batch
from senthire.screening.pricing import estimate_usd
from senthire.screening.schemas import DeepAnalysisOutput, LightScreenOutput

MINIMAL_SPEC = EvaluationSpec.model_validate(
    {
        "schema_version": "1.0",
        "weights": {"relevant_experience": 1.0},
        "requirements": [
            {
                "req_id": "R1",
                "category": "relevant_experience",
                "label": {"tr": "B2B satış"},
                "type": "scored",
                "evaluator": "semantic",
                "semantic": {"rubric": "Score B2B sales depth."},
            }
        ],
    }
)
PROFILE = {"derived": {}, "location": {}, "experience": []}


def test_sanitize_schema_strips_unsupported_keywords():
    raw = {
        "type": "object",
        "properties": {
            "score": {"type": "number", "minimum": 0, "maximum": 1, "default": 0},
            "items": {"type": "array", "minItems": 1, "items": {"type": "string", "pattern": "^a"}},
        },
        "additionalProperties": False,
        "required": ["score"],
    }
    clean = batch.sanitize_schema(raw)
    assert clean["additionalProperties"] is False  # allowlist keyword survives
    assert clean["required"] == ["score"]
    assert clean["properties"]["score"] == {"type": "number"}
    assert clean["properties"]["items"] == {"type": "array", "items": {"type": "string"}}


def test_output_format_shape_matches_structured_outputs_contract():
    fmt = batch.output_format(LightScreenOutput)
    assert fmt["type"] == "json_schema"
    schema = fmt["schema"]
    assert schema["type"] == "object"
    # extra="forbid" must survive sanitizing — structured outputs require it
    assert schema["additionalProperties"] is False
    serialized = json.dumps(schema)
    for banned in ("minimum", "maxLength", "pattern"):
        assert banned not in serialized


def test_light_request_carries_custom_id_cache_breakpoint_and_format():
    request = batch.light_request("app-123", MINIMAL_SPEC, PROFILE)
    assert request["custom_id"] == "app-123"
    params = request["params"]
    assert params["model"] and params["max_tokens"] > 0
    content = params["messages"][0]["content"]
    # the spec block is cached; the per-candidate profile block is not
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in content[1]
    assert params["output_config"]["format"]["type"] == "json_schema"


def test_deep_request_includes_raw_text_and_light_judgments():
    request = batch.deep_request(
        "app-9", MINIMAL_SPEC, PROFILE, "CV RAW TEXT HERE", [{"req_id": "R1", "verdict": "met"}]
    )
    body = json.dumps(request["params"]["messages"][0]["content"], ensure_ascii=False)
    assert "CV RAW TEXT HERE" in body
    assert "R1" in body
    assert request["params"]["output_config"]["format"]["schema"]["additionalProperties"] is False


def test_batch_and_interactive_send_identical_spec_blocks():
    """Prompt-cache correctness: the cached prefix must be byte-identical."""
    from senthire.screening.llm import light_content

    interactive = light_content(MINIMAL_SPEC, PROFILE)
    batched = batch.light_request("x", MINIMAL_SPEC, PROFILE)["params"]["messages"][0]["content"]
    assert interactive == batched


class _Usage:
    input_tokens = 1000
    output_tokens = 200
    cache_read_input_tokens = 5000
    cache_creation_input_tokens = 0


class _TextBlock:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _Message:
    stop_reason = "end_turn"

    def __init__(self, text: str):
        self.content = [_TextBlock(text)]
        self.usage = _Usage()


def test_iter_results_parses_succeeded_and_isolates_failures(monkeypatch):
    good = json.dumps(
        {
            "judgments": [
                {
                    "req_id": "R1",
                    "verdict": "met",
                    "confidence": 0.9,
                    "info_status": "explicit",
                    "reasoning": "clear",
                }
            ]
        }
    )

    class _Result:
        def __init__(self, type_, message=None, error=None):
            self.type = type_
            self.message = message
            self.error = error

    class _Entry:
        def __init__(self, custom_id, result):
            self.custom_id = custom_id
            self.result = result

    class _Error:
        type = "invalid_request"

    entries = [
        _Entry("ok-1", _Result("succeeded", _Message(good))),
        _Entry("bad-json", _Result("succeeded", _Message("not json"))),
        _Entry("errored", _Result("errored", error=_Error())),
        _Entry("expired", _Result("expired")),
    ]

    class _Batches:
        def results(self, batch_id):
            return iter(entries)

    class _Messages:
        batches = _Batches()

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(batch.anthropic, "Anthropic", lambda: _Client())
    outcomes = {o.custom_id: o for o in batch.iter_results("mb_1", LightScreenOutput)}

    assert outcomes["ok-1"].output.judgments[0].verdict == "met"
    assert outcomes["ok-1"].usage.cache_read_tokens == 5000
    # a malformed or failed request degrades to an error, never an exception
    assert outcomes["bad-json"].output is None and outcomes["bad-json"].error
    assert "invalid_request" in outcomes["errored"].error
    assert "expired" in outcomes["expired"].error


def test_iter_results_treats_refusal_as_a_failed_row(monkeypatch):
    class _Refused(_Message):
        stop_reason = "refusal"

    class _Entry:
        custom_id = "r1"

        class result:
            type = "succeeded"
            message = _Refused("{}")

    class _Client:
        class messages:
            class batches:
                @staticmethod
                def results(batch_id):
                    return iter([_Entry()])

    monkeypatch.setattr(batch.anthropic, "Anthropic", lambda: _Client())
    (outcome,) = list(batch.iter_results("mb_2", DeepAnalysisOutput))
    assert outcome.output is None and "refus" in outcome.error


def test_pricing_estimate_discounts_cache_reads():
    detail = {
        "model": "claude-haiku-4-5",
        "input_tokens": 1_000_000,
        "output_tokens": 0,
        "cache_read_tokens": 0,
    }
    assert estimate_usd(detail) == 1.0
    cached = {**detail, "input_tokens": 0, "cache_read_tokens": 1_000_000}
    assert abs(estimate_usd(cached) - 0.10) < 1e-9
    unknown_model = {**detail, "model": "made-up"}
    assert estimate_usd(unknown_model) > 0  # falls back rather than crashing
