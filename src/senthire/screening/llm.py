"""Stage 4/5 model calls (docs/02). Prompt-cache-aware, structured outputs.

Serialization is deterministic (sort_keys) so the spec block is byte-identical
across the fan-out and the provider prompt cache actually hits (docs/07 §4).
"""

import json
from dataclasses import dataclass

import anthropic

from senthire.config import get_settings
from senthire.domain.spec import EvaluationSpec
from senthire.screening import prompts
from senthire.screening.deterministic import semantic_requirements
from senthire.screening.schemas import DeepAnalysisOutput, LightScreenOutput

LIGHT_MAX_TOKENS = 4096
DEEP_MAX_TOKENS = 6144


class ScreeningCallFailed(RuntimeError):
    pass


@dataclass(frozen=True)
class LlmUsage:
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


def _spec_block(spec: EvaluationSpec) -> str:
    return json.dumps(semantic_requirements(spec), ensure_ascii=False, sort_keys=True)


def _profile_block(profile: dict) -> str:
    return json.dumps(profile, ensure_ascii=False, sort_keys=True)


def _call(model: str, system: str, content: list[dict], output_format, max_tokens: int):
    try:
        response = anthropic.Anthropic().messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=output_format,
        )
    except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError):
        raise  # transient — task layer retries with backoff
    except anthropic.APIStatusError as exc:
        raise ScreeningCallFailed(f"{exc.status_code} {exc.message}") from exc
    parsed = response.parsed_output
    if parsed is None:
        raise ScreeningCallFailed("model returned no parseable output")
    usage = LlmUsage(
        model=model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
    )
    return parsed, usage


def light_content(spec: EvaluationSpec, profile: dict) -> list[dict]:
    """Stage 4 user content. Shared by the interactive and batch transports so both
    send byte-identical spec blocks and hit the same prompt cache."""
    return [
        {
            "type": "text",
            "text": prompts.LIGHT_USER_SPEC.format(spec_json=_spec_block(spec)),
            # breakpoint covers system + spec → written once per job, read per candidate
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": prompts.LIGHT_USER_PROFILE.format(profile_json=_profile_block(profile))},
    ]


def deep_content(
    spec: EvaluationSpec, profile: dict, raw_text: str, light_judgments: list[dict]
) -> list[dict]:
    """Stage 5 user content (see light_content for why this is shared)."""
    return [
        {
            "type": "text",
            "text": prompts.DEEP_USER_CONTEXT.format(spec_json=_spec_block(spec)),
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": prompts.DEEP_USER_CANDIDATE.format(
                profile_json=_profile_block(profile),
                raw_text=raw_text,
                light_json=json.dumps(light_judgments, ensure_ascii=False, sort_keys=True),
            ),
        },
    ]


def light_screen(spec: EvaluationSpec, profile: dict) -> tuple[LightScreenOutput, LlmUsage]:
    settings = get_settings()
    return _call(
        settings.light_screen_model,
        prompts.LIGHT_SYSTEM,
        light_content(spec, profile),
        LightScreenOutput,
        LIGHT_MAX_TOKENS,
    )


def deep_analyze(
    spec: EvaluationSpec,
    profile: dict,
    raw_text: str,
    light_judgments: list[dict],
) -> tuple[DeepAnalysisOutput, LlmUsage]:
    settings = get_settings()
    return _call(
        settings.deep_analysis_model,
        prompts.DEEP_SYSTEM,
        deep_content(spec, profile, raw_text, light_judgments),
        DeepAnalysisOutput,
        DEEP_MAX_TOKENS,
    )
