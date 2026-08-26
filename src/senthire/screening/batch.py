"""Batch transport for Stages 4 and 5 — the economy mode (docs/07 §5, docs/08 §5).

The Message Batches API processes requests asynchronously (most batches finish
well inside an hour; the hard ceiling is 24h) at **half** the token price. The
funnel is already queue-driven and idempotent, so the only difference from the
interactive transport is *when* results arrive: instead of one blocking call per
candidate, we submit the whole fan-out at once and poll.

Two invariants make this safe to mix with the interactive path:

- The user content is built by the same ``llm.light_content`` / ``llm.deep_content``
  helpers, so the cached spec prefix is byte-identical either way.
- ``custom_id`` carries the application id, because results come back in
  arbitrary order (never key them by position).

Structured outputs work in batches, but ``messages.parse`` does not — that helper
is for single calls. Here we send ``output_config.format`` explicitly and validate
the returned JSON against the same Pydantic model, so both transports produce
identical objects.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import anthropic
from pydantic import BaseModel, ValidationError

from senthire.config import get_settings
from senthire.domain.spec import EvaluationSpec
from senthire.screening import prompts
from senthire.screening.llm import (
    DEEP_MAX_TOKENS,
    LIGHT_MAX_TOKENS,
    LlmUsage,
    ScreeningCallFailed,
    deep_content,
    light_content,
)
from senthire.screening.schemas import DeepAnalysisOutput, LightScreenOutput

# Structured outputs reject these JSON Schema keywords; Pydantic emits some of
# them from field metadata. Stripping keeps the schema accepted without changing
# what the model is asked for (the constraints are re-checked on validation).
_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minProperties",
        "maxProperties",
        "format",
        "default",
    }
)


def sanitize_schema(node: Any) -> Any:
    """Recursively drop keywords structured outputs does not accept."""
    if isinstance(node, dict):
        return {
            key: sanitize_schema(value)
            for key, value in node.items()
            if key not in _UNSUPPORTED_SCHEMA_KEYS
        }
    if isinstance(node, list):
        return [sanitize_schema(item) for item in node]
    return node


def output_format(model: type[BaseModel]) -> dict:
    return {"type": "json_schema", "schema": sanitize_schema(model.model_json_schema())}


def light_request(custom_id: str, spec: EvaluationSpec, profile: dict) -> dict:
    settings = get_settings()
    return {
        "custom_id": custom_id,
        "params": {
            "model": settings.light_screen_model,
            "max_tokens": LIGHT_MAX_TOKENS,
            "temperature": settings.judge_temperature,
            "system": prompts.LIGHT_SYSTEM,
            "messages": [{"role": "user", "content": light_content(spec, profile)}],
            "output_config": {"format": output_format(LightScreenOutput)},
        },
    }


def deep_request(
    custom_id: str,
    spec: EvaluationSpec,
    profile: dict,
    raw_text: str,
    light_judgments: list[dict],
) -> dict:
    settings = get_settings()
    return {
        "custom_id": custom_id,
        "params": {
            "model": settings.deep_analysis_model,
            "max_tokens": DEEP_MAX_TOKENS,
            "temperature": settings.judge_temperature,
            "system": prompts.DEEP_SYSTEM,
            "messages": [
                {
                    "role": "user",
                    "content": deep_content(spec, profile, raw_text, light_judgments),
                }
            ],
            "output_config": {"format": output_format(DeepAnalysisOutput)},
        },
    }


def submit(requests: list[dict]) -> str:
    """Create a batch and return its id. Raises on a permanently bad request."""
    try:
        batch = anthropic.Anthropic().messages.batches.create(requests=requests)
    except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError):
        raise  # transient — the task layer retries with backoff
    except anthropic.APIStatusError as exc:
        raise ScreeningCallFailed(f"batch create: {exc.status_code} {exc.message}") from exc
    return batch.id


def processing_status(batch_id: str) -> str:
    """'in_progress' | 'canceling' | 'ended' (results are only readable at 'ended')."""
    return anthropic.Anthropic().messages.batches.retrieve(batch_id).processing_status


@dataclass
class BatchOutcome:
    """One request's result. Exactly one of `output` / `error` is set."""

    custom_id: str
    output: BaseModel | None = None
    usage: LlmUsage | None = None
    error: str | None = None


def _text_of(message) -> str:
    return "".join(block.text for block in message.content if block.type == "text")


def _usage_of(message, model: str) -> LlmUsage:
    usage = message.usage
    return LlmUsage(
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )


def iter_results(batch_id: str, output_model: type[BaseModel]) -> Iterator[BatchOutcome]:
    """Stream the finished batch, validating each success against `output_model`.

    Per-request failures are yielded as errors rather than raised: one bad
    candidate must not sink the run (docs/08 §6).
    """
    settings = get_settings()
    model = (
        settings.light_screen_model
        if output_model is LightScreenOutput
        else settings.deep_analysis_model
    )
    for entry in anthropic.Anthropic().messages.batches.results(batch_id):
        result = entry.result
        if result.type != "succeeded":
            detail = getattr(getattr(result, "error", None), "type", result.type)
            yield BatchOutcome(custom_id=entry.custom_id, error=f"batch result {detail}")
            continue
        message = result.message
        if message.stop_reason == "refusal":
            yield BatchOutcome(custom_id=entry.custom_id, error="model refused the request")
            continue
        try:
            parsed = output_model.model_validate_json(_text_of(message))
        except (ValidationError, json.JSONDecodeError) as exc:
            yield BatchOutcome(
                custom_id=entry.custom_id, error=f"unparseable batch output: {exc}"
            )
            continue
        yield BatchOutcome(
            custom_id=entry.custom_id, output=parsed, usage=_usage_of(message, model)
        )
