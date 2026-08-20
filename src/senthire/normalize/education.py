"""Degrees, institutions and fields of study, normalized.

Degree level is the one that reaches a predicate (`derived.highest_degree_rank`),
so reading "Y. Lisans" or "MBA" correctly is the difference between a master's
graduate passing a bachelor's floor and being marked unknown.
"""

from dataclasses import dataclass
from functools import cache

from senthire.normalize import text
from senthire.normalize.tables import table


@dataclass(frozen=True)
class EducationMatch:
    degree: str | None = None
    institution: str | None = None
    field: str | None = None


@cache
def _degree_index() -> list[tuple[tuple[str, ...], str]]:
    return [(tuple(a), t) for a, t in text.build_index(table("education")["degrees"])]


@cache
def _institution_index() -> list[tuple[tuple[str, ...], str]]:
    aliases = table("education")["institution_aliases"]
    mapping = {name: [name, *alias_list] for name, alias_list in aliases.items()}
    return [(tuple(a), t) for a, t in text.build_index(mapping)]


@cache
def _field_index() -> list[tuple[tuple[str, ...], str]]:
    aliases = table("education")["field_aliases"]
    mapping = {name: [name, *alias_list] for name, alias_list in aliases.items()}
    return [(tuple(a), t) for a, t in text.build_index(mapping)]


def _hit(raw: str | None, index) -> str | None:
    if not raw:
        return None
    return text.longest_match(text.tokens(raw), [(list(a), t) for a, t in index])


def classify(*, degree_raw: str | None, institution: str | None, field: str | None) -> EducationMatch:
    return EducationMatch(
        degree=_hit(degree_raw, _degree_index()),
        institution=_hit(institution, _institution_index()),
        field=_hit(field, _field_index()),
    )
