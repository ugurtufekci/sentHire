"""Stage 1 extractor: document bytes → validated ExtractedProfile (docs/02 Stage 1).

Uses Claude with structured outputs (`messages.parse`) so malformed JSON is an
API-layer retry, not a parsing bug. Model tier per docs/07 §1 (Haiku 4.5 by
default; escalation model on low confidence is wired by the caller/config).
"""

import base64
from dataclasses import dataclass

import anthropic

from senthire.config import get_settings
from senthire.domain.profile import ExtractedProfile
from senthire.extraction import prompts
from senthire.extraction.pdf import PdfAnalysis, analyze_pdf

MAX_OUTPUT_TOKENS = 8192
LOW_CONFIDENCE_THRESHOLD = 0.5


@dataclass(frozen=True)
class ExtractionOutcome:
    profile: ExtractedProfile
    raw_text: str
    path: str  # "text" | "vision"
    model: str
    prompt_version: str
    page_count: int
    input_tokens: int
    output_tokens: int


class ExtractionFailed(RuntimeError):
    pass


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()  # ANTHROPIC_API_KEY from the environment


def extract_pdf(data: bytes, *, escalated: bool = False) -> ExtractionOutcome:
    if get_settings().fake_models:
        from senthire.demo.models import extract_pdf as offline

        return offline(data, escalated=escalated)

    settings = get_settings()
    analysis = analyze_pdf(data)
    if analysis.page_count == 0:
        raise ExtractionFailed("PDF has no pages")
    if analysis.page_count > settings.max_pdf_pages:
        raise ExtractionFailed(f"PDF has {analysis.page_count} pages (max {settings.max_pdf_pages})")

    model = settings.extraction_escalation_model if escalated else settings.extraction_model
    prompt_version = settings.prompt_versions["extract"]

    if analysis.has_text_layer:
        outcome = _extract_text_path(analysis, model, prompt_version)
    else:
        outcome = _extract_vision_path(data, analysis, model, prompt_version)

    # One-shot escalation to the stronger tier on very low self-reported
    # confidence (docs/02 Stage 1) — never loops.
    confidence = outcome.profile.confidence
    if (
        not escalated
        and confidence is not None
        and confidence < LOW_CONFIDENCE_THRESHOLD
        and settings.extraction_escalation_model != model
    ):
        return extract_pdf(data, escalated=True)
    return outcome


def _parse(messages: list[dict], model: str) -> tuple[ExtractedProfile, int, int]:
    try:
        response = _client().messages.parse(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=prompts.EXTRACTION_SYSTEM,
            messages=messages,
            output_format=ExtractedProfile,
        )
    except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError):
        raise  # transient — the task layer retries with backoff (docs/08 §3)
    except anthropic.APIStatusError as exc:  # other 4xx: permanent for this document
        raise ExtractionFailed(f"model call failed: {exc.status_code} {exc.message}") from exc
    profile = response.parsed_output
    if profile is None:
        raise ExtractionFailed("model returned no parseable profile")
    return profile, response.usage.input_tokens, response.usage.output_tokens


def _extract_text_path(analysis: PdfAnalysis, model: str, prompt_version: str) -> ExtractionOutcome:
    profile, tokens_in, tokens_out = _parse(
        [{"role": "user", "content": prompts.TEXT_PATH_USER.format(text=analysis.text)}], model
    )
    return ExtractionOutcome(
        profile=profile,
        raw_text=analysis.text,
        path="text",
        model=model,
        prompt_version=prompt_version,
        page_count=analysis.page_count,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
    )


def _extract_vision_path(
    data: bytes, analysis: PdfAnalysis, model: str, prompt_version: str
) -> ExtractionOutcome:
    content = [
        {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(data).decode(),
            },
        },
        {"type": "text", "text": prompts.VISION_PATH_USER},
    ]
    profile, tokens_in, tokens_out = _parse([{"role": "user", "content": content}], model)
    raw_text = profile.full_text or ""
    return ExtractionOutcome(
        profile=profile,
        raw_text=raw_text,
        path="vision",
        model=model,
        prompt_version=prompt_version,
        page_count=analysis.page_count,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
    )
