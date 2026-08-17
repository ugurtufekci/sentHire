"""Structured-output schemas for Stages 4 & 5 (what the judging models emit)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceQuote(_Strict):
    quote: str  # verbatim snippet from the profile/CV text
    page: int | None = None


class ReqJudgment(_Strict):
    req_id: str
    verdict: Literal["met", "partially_met", "not_met", "unknown"]
    score: float | None = None  # 0..1 graded satisfaction (null when unknown)
    confidence: float  # 0..1, honest
    info_status: Literal["explicit", "inferred", "ambiguous", "missing"]
    evidence: list[EvidenceQuote] = []
    reasoning: str  # one or two sentences, plain language


class LightScreenOutput(_Strict):
    judgments: list[ReqJudgment]
    strengths: list[str] = []
    weaknesses: list[str] = []
    red_flags: list[str] = []  # surfaced, never auto-penalized (docs/09 §2)


class DeepCorrection(_Strict):
    req_id: str
    from_verdict: Literal["met", "partially_met", "not_met", "unknown"]
    to_verdict: Literal["met", "partially_met", "not_met", "unknown"]
    note: str


class DeepAnalysisOutput(_Strict):
    judgments: list[ReqJudgment]  # re-judged semantic requirements (all of them)
    corrections: list[DeepCorrection] = []  # where the light pass was wrong
    strengths: list[str] = []
    weaknesses: list[str] = []
    missing_information: list[str] = []
    summary: str | None = None  # 2–3 sentences for the reviewer, labeled AI-generated
