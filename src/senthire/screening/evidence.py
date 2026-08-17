"""Evidence verification (docs/06 §4) — hallucinated quotes never survive.

A quote is verified iff its normalized form appears in the normalized source
text. Failed quotes are dropped; a judgment left with no evidence is degraded
(confidence capped, info_status → ambiguous) rather than trusted.
"""

import re
from dataclasses import dataclass

from senthire.screening.schemas import ReqJudgment

_WS = re.compile(r"\s+")
DEGRADED_CONFIDENCE_CAP = 0.4
MIN_QUOTE_CHARS = 8


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip().lower()


@dataclass
class VerifiedJudgment:
    judgment: ReqJudgment
    dropped_quotes: int = 0
    degraded: bool = False


def verify_judgment(judgment: ReqJudgment, source_text: str) -> VerifiedJudgment:
    if judgment.verdict == "unknown":
        return VerifiedJudgment(judgment=judgment)

    haystack = _norm(source_text)
    kept, dropped = [], 0
    for quote in judgment.evidence:
        normalized = _norm(quote.quote)
        if len(normalized) >= MIN_QUOTE_CHARS and normalized in haystack:
            kept.append(quote)
        else:
            dropped += 1

    if kept or not judgment.evidence:
        updated = judgment.model_copy(update={"evidence": kept})
        # A decisive verdict with zero evidence offered at all is suspicious too
        degraded = not kept
        if degraded:
            updated = updated.model_copy(
                update={
                    "confidence": min(updated.confidence, DEGRADED_CONFIDENCE_CAP),
                    "info_status": "ambiguous",
                }
            )
        return VerifiedJudgment(judgment=updated, dropped_quotes=dropped, degraded=degraded)

    # every quote failed verification → the verdict itself is not trustworthy
    degraded_judgment = judgment.model_copy(
        update={
            "evidence": [],
            "confidence": min(judgment.confidence, DEGRADED_CONFIDENCE_CAP),
            "info_status": "ambiguous",
        }
    )
    return VerifiedJudgment(judgment=degraded_judgment, dropped_quotes=dropped, degraded=True)


def verify_all(judgments: list[ReqJudgment], source_text: str) -> tuple[list[ReqJudgment], dict]:
    verified, dropped, degraded = [], 0, []
    for judgment in judgments:
        result = verify_judgment(judgment, source_text)
        verified.append(result.judgment)
        dropped += result.dropped_quotes
        if result.degraded:
            degraded.append(judgment.req_id)
    stats = {"dropped_quotes": dropped, "degraded_req_ids": degraded}
    return verified, stats
