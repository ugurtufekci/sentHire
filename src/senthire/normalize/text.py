"""Turkish-aware text folding — the small thing everything else depends on.

`str.lower()` is wrong for Turkish: "İSTANBUL".lower() leaves a combining dot,
and "ISPARTA".lower() gives "ısparta" in the locale sense but "isparta" in
Python's. Matching a CV's "İZMİR" against a table's "izmir" therefore fails in
ways that look like missing data rather than a bug — so every comparison in
this package goes through fold().
"""

import re
import unicodedata

_TURKISH = str.maketrans(
    {
        "İ": "i", "I": "i", "ı": "i", "Ş": "s", "ş": "s", "Ğ": "g", "ğ": "g",
        "Ü": "u", "ü": "u", "Ö": "o", "ö": "o", "Ç": "c", "ç": "c", "Â": "a",
        "â": "a", "Î": "i", "î": "i", "Û": "u", "û": "u",
    }
)
_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


def fold(value: str) -> str:
    """Lowercase, de-diacritic, punctuation → spaces, whitespace collapsed."""
    if not value:
        return ""
    folded = value.translate(_TURKISH).lower()
    folded = unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode()
    return _NON_WORD.sub(" ", folded).strip()


def tokens(value: str) -> list[str]:
    folded = fold(value)
    return folded.split() if folded else []


# Turkish glues suffixes onto nouns, so a CV says "Bankası", "Lojistiği",
# "Satışı" where the vocabulary says "banka", "lojistik", "satış". Matching
# only exact tokens loses those; matching loose prefixes invents matches. The
# bounds below are the compromise: a real word (>= MIN_STEM) may grow by at
# most MAX_SUFFIX characters, and a final hard consonant may soften (k→ğ, p→b,
# t→d, ç→c) as Turkish requires before a vowel.
MIN_STEM = 4
MAX_SUFFIX = 4
_SOFTENED = {"k": "g", "p": "b", "t": "d", "c": "c", "g": "g"}


def token_matches(candidate: str, alias: str) -> bool:
    if candidate == alias:
        return True
    if len(alias) < MIN_STEM or not 0 < len(candidate) - len(alias) <= MAX_SUFFIX:
        return False
    if candidate.startswith(alias):
        return True
    # "lojistik" + "-i" surfaces as "lojistigi": the stem's last letter softens.
    softened = _SOFTENED.get(alias[-1])
    return softened is not None and candidate.startswith(alias[:-1] + softened)


def contains_run(haystack: list[str], needle: list[str]) -> bool:
    """True when `needle` appears as a contiguous token run in `haystack`.

    Token runs rather than substrings: "as" (a legal-suffix token) must not
    match inside "asistan", and "ing" must not match inside "mühendis".
    Individual tokens may carry Turkish suffixes (see token_matches).
    """
    if not needle or len(needle) > len(haystack):
        return False
    span = len(needle)
    return any(
        all(token_matches(haystack[i + offset], part) for offset, part in enumerate(needle))
        for i in range(len(haystack) - span + 1)
    )


def build_index(mapping: dict[str, list[str]]) -> list[tuple[list[str], str]]:
    """alias-lists keyed by target → [(alias_tokens, target)], longest first.

    Longest-first is what makes "orta ileri" beat "orta" and "kurumsal satış"
    beat "satış": the more specific alias must win regardless of table order.
    """
    index = [
        (tokens(alias), target)
        for target, aliases in mapping.items()
        for alias in aliases
        if tokens(alias)
    ]
    return sorted(index, key=lambda pair: (-len(pair[0]), pair[1]))


def longest_match(value_tokens: list[str], index: list[tuple[list[str], str]]) -> str | None:
    for alias_tokens, target in index:
        if contains_run(value_tokens, alias_tokens):
            return target
    return None


def strip_runs(value_tokens: list[str], runs: list[list[str]]) -> list[str]:
    """Remove every occurrence of the given token runs (longest first)."""
    out = list(value_tokens)
    for run in sorted(runs, key=len, reverse=True):
        span = len(run)
        index = 0
        while index <= len(out) - span:
            if out[index : index + span] == run:
                del out[index : index + span]
            else:
                index += 1
    return out
