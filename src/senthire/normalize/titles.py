"""Job title → (family, seniority).

Two axes, deliberately separate: "Junior Account Executive" and "Account
Executive Director" are the same *family* and wildly different *seniority*, and
collapsing them into one string is how title matching goes wrong.
"""

from dataclasses import dataclass
from functools import cache

from senthire.normalize import text
from senthire.normalize.tables import table

SENIORITY_ORDER = [
    "intern", "junior", "mid", "senior", "lead", "manager", "director", "executive_suite",
]


@dataclass(frozen=True)
class TitleMatch:
    family: str | None
    label: str | None
    seniority: str | None
    matched_alias: str | None = None


@cache
def _family_index() -> list[tuple[tuple[str, ...], str]]:
    families = table("titles")["families"]
    index = text.build_index({f["id"]: f["aliases"] for f in families})
    return [(tuple(alias), target) for alias, target in index]


@cache
def _seniority_index() -> list[tuple[tuple[str, ...], str]]:
    markers = table("titles")["seniority_markers"]
    return [(tuple(alias), target) for alias, target in text.build_index(markers)]


def _best_family(value_tokens: list[str]) -> tuple[str | None, str | None]:
    """Longest alias wins; the catch-all family only answers when nothing else
    does, so "Tele Satış Temsilcisi" is inside sales, not merely sales."""
    best: tuple[str | None, str | None] = (None, None)
    for alias, target in _family_index():
        if not text.contains_run(value_tokens, list(alias)):
            continue
        if target in _fallback_families():
            best = best if best[0] else (target, " ".join(alias))
            continue
        return target, " ".join(alias)
    return best


@cache
def _fallback_families() -> frozenset[str]:
    return frozenset(
        f["id"] for f in table("titles")["families"] if f.get("fallback")
    )


@cache
def _labels() -> dict[str, str]:
    return {f["id"]: f["label_tr"] for f in table("titles")["families"]}


def classify(title_raw: str | None) -> TitleMatch:
    if not title_raw:
        return TitleMatch(None, None, None)
    raw_tokens = text.tokens(title_raw)

    seniority = None
    seniority_runs: list[list[str]] = []
    for alias, level in _seniority_index():
        if text.contains_run(raw_tokens, list(alias)):
            seniority_runs.append(list(alias))
            # The most senior marker present wins: "Kıdemli Satış Müdürü" is a
            # manager, not a senior individual contributor.
            if seniority is None or SENIORITY_ORDER.index(level) > SENIORITY_ORDER.index(seniority):
                seniority = level

    # Family matching considers the title both as written and with seniority
    # words removed. Removing them lets "Satış Müdürü" reach the same family as
    # "Satış Uzmanı"; keeping them lets "Sistem Yöneticisi" match a family
    # alias that legitimately contains one. The longer match wins.
    core = text.strip_runs(raw_tokens, seniority_runs)
    family, alias_hit = _best_family(raw_tokens)
    if core and core != raw_tokens:
        core_family, core_alias = _best_family(core)
        if core_family and (family is None or len(core_alias or "") > len(alias_hit or "")):
            family, alias_hit = core_family, core_alias

    return TitleMatch(
        family=family,
        label=_labels().get(family) if family else None,
        seniority=seniority,
        matched_alias=alias_hit,
    )
