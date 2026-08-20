"""Language level prose and exam scores → CEFR.

The predicate registry compares `languages['en'].cefr_rank`, so a CV saying
"İngilizce: iyi derecede" or "YDS: 78" must reach the same axis as one that
helpfully writes "B2". Otherwise the requirement silently reads as unknown.
"""

import re
from dataclasses import dataclass
from functools import cache

from senthire.normalize import text
from senthire.normalize.tables import table

_SCORE = re.compile(r"(\d{1,3}(?:[.,]\d)?)")


@dataclass(frozen=True)
class LevelMatch:
    cefr: str | None
    source: str | None = None  # "phrase" | "exam:<name>" | "certificate"


@cache
def _phrase_index() -> list[tuple[tuple[str, ...], str]]:
    return [(tuple(a), t) for a, t in text.build_index(table("languages")["phrases"])]


@cache
def _language_index() -> list[tuple[tuple[str, ...], str]]:
    return [(tuple(a), t) for a, t in text.build_index(table("languages")["language_names"])]


@cache
def _certificate_index() -> list[tuple[tuple[str, ...], str]]:
    certs = table("languages")["certificates"]
    return [(tuple(text.tokens(name)), cefr) for name, cefr in certs.items()]


def language_code(raw: str | None) -> str | None:
    """'İngilizce' / 'English' / 'en' → 'en'."""
    if not raw:
        return None
    return text.longest_match(text.tokens(raw), [(list(a), t) for a, t in _language_index()])


def level(raw: str | None) -> LevelMatch:
    """Read a level from free text: a phrase, an exam score, or a certificate."""
    if not raw:
        return LevelMatch(None)
    value_tokens = text.tokens(raw)

    for exam, spec in table("languages")["exams"].items():
        if not any(text.contains_run(value_tokens, text.tokens(a)) for a in spec["aliases"]):
            continue
        match = _SCORE.search(text.fold(raw))
        if match is None:
            continue
        score = float(match.group(1).replace(",", "."))
        for threshold, cefr in spec["bands"]:
            if score >= threshold:
                return LevelMatch(cefr, source=f"exam:{exam}")
        return LevelMatch("A1", source=f"exam:{exam}")

    for alias, cefr in _certificate_index():
        if text.contains_run(value_tokens, list(alias)):
            return LevelMatch(cefr, source="certificate")

    hit = text.longest_match(value_tokens, [(list(a), t) for a, t in _phrase_index()])
    return LevelMatch(hit, source="phrase" if hit else None)
