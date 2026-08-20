"""Company names → sector.

'Örnek Lojistik San. ve Tic. A.Ş.' is a logistics company; the legal suffixes
are noise that defeats every kind of matching, so they come off first.
"""

from functools import cache

from senthire.normalize import text
from senthire.normalize.tables import table


@cache
def _sector_index() -> list[tuple[tuple[str, ...], str]]:
    return [(tuple(a), t) for a, t in text.build_index(table("industry")["sectors"])]


@cache
def _suffix_runs() -> list[list[str]]:
    return [text.tokens(s) for s in table("industry")["legal_suffixes"] if text.tokens(s)]


def strip_legal_suffixes(company: str | None) -> str:
    if not company:
        return ""
    return " ".join(text.strip_runs(text.tokens(company), _suffix_runs()))


def sector(*parts: str | None) -> str | None:
    """Read a sector from any of company name, stated industry, description."""
    for part in parts:
        if not part:
            continue
        stripped = text.tokens(strip_legal_suffixes(part)) or text.tokens(part)
        hit = text.longest_match(stripped, [(list(a), t) for a, t in _sector_index()])
        if hit:
            return hit
    return None
