"""A deterministic stand-in for the labeling oracle — no API, no cost, no drift.

It exists so the whole corpus flow (import → label → review → promote) can be
exercised offline: in tests, in CI, and by anyone trying the tooling before
spending a lira on model calls. It judges by looking for the rubric's own words
in the profile, and it deliberately produces a *split* when the evidence is
thin, so the adjudication path is exercised rather than assumed.
"""

import json
import re

from senthire.domain.spec import EvaluationSpec
from senthire.screening.schemas import EvidenceQuote, LightScreenOutput, ReqJudgment

STOPWORDS = {
    "the", "and", "for", "with", "score", "candidate", "experience", "ile", "ve",
    "puan", "aday", "deneyim", "olan", "var", "bir",
}


def _keywords(rubric: str) -> list[str]:
    words = re.findall(r"\w{4,}", rubric.lower())
    return [w for w in words if w not in STOPWORDS][:6]


def deterministic_oracle(spec: EvaluationSpec, profile: dict, lens: str) -> LightScreenOutput:
    haystack = json.dumps(profile, ensure_ascii=False).lower()
    judgments = []
    for req in spec.requirements:
        if req.evaluator not in {"semantic", "hybrid"} or req.semantic is None:
            continue
        hits = [k for k in _keywords(req.semantic.rubric) if k in haystack]
        strength = len(hits) / max(1, len(_keywords(req.semantic.rubric)))
        if strength >= 0.5:
            verdict, score = "met", 1.0
        elif strength > 0:
            # Thin evidence: the three lenses legitimately differ, which is
            # exactly the case a human should decide.
            verdict, score = {
                "advocate": ("met", 0.8),
                "skeptic": ("not_met", 0.1),
            }.get(lens, ("partially_met", 0.5))
        else:
            verdict, score = "unknown", None
        judgments.append(
            ReqJudgment(
                req_id=req.req_id,
                verdict=verdict,
                score=score,
                confidence=0.9 if verdict != "unknown" else 0.4,
                info_status="explicit" if hits else "missing",
                evidence=[EvidenceQuote(quote=hits[0])] if hits else [],
                reasoning=f"fake oracle ({lens}): matched {hits or 'nothing'}",
            )
        )
    return LightScreenOutput(judgments=judgments)
