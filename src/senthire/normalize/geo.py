"""Location strings → a Turkish province, plus an explicit relocation signal.

HR writes the requirement as "Ankara". CVs write "Çankaya", "Ostim",
"Ankara/Türkiye", or "ANKARA - ÇANKAYA". Without this table the deterministic
location predicate quietly answers "unknown" for candidates who plainly live
there, which is the most expensive kind of silent failure: it looks like
missing data, and missing data is (correctly) not held against anyone.
"""

from dataclasses import dataclass
from functools import cache

from senthire.normalize import text
from senthire.normalize.tables import table


@dataclass(frozen=True)
class LocationMatch:
    province: str | None
    via: str | None = None  # "province" | "alias" | "district"
    district: str | None = None


@cache
def _province_index() -> list[tuple[tuple[str, ...], str]]:
    data = table("geo")
    mapping: dict[str, list[str]] = {p: [p] for p in data["provinces"]}
    for province, aliases in data["province_aliases"].items():
        mapping.setdefault(province, []).extend(aliases)
    return [(tuple(a), t) for a, t in text.build_index(mapping)]


@cache
def _district_index() -> list[tuple[tuple[str, ...], tuple[str, str]]]:
    data = table("geo")
    index = []
    for province, districts in data["districts"].items():
        for district in districts:
            index.append((tuple(text.tokens(district)), (province, district)))
    return sorted(index, key=lambda pair: (-len(pair[0]), pair[1]))


def resolve(raw: str | None) -> LocationMatch:
    if not raw:
        return LocationMatch(None)
    value_tokens = text.tokens(raw)

    # Provinces first: "Ankara Çankaya" is Ankara either way, and a district
    # name that duplicates a province name must not outrank the province.
    for alias, province in _province_index():
        if text.contains_run(value_tokens, list(alias)):
            return LocationMatch(province, via="province" if len(alias) else "alias")
    for alias, (province, district) in _district_index():
        if text.contains_run(value_tokens, list(alias)):
            return LocationMatch(province, via="district", district=district)
    return LocationMatch(None)


def relocation_signal(raw_text: str | None) -> bool | None:
    """None means the CV doesn't say — which is not the same as 'won't move'."""
    if not raw_text:
        return None
    value_tokens = text.tokens(raw_text)
    data = table("geo")
    for phrase in data["relocation_no"]:
        if text.contains_run(value_tokens, text.tokens(phrase)):
            return False
    for phrase in data["relocation_yes"]:
        if text.contains_run(value_tokens, text.tokens(phrase)):
            return True
    return None
