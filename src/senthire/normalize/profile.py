"""Apply the vocabularies to an extracted profile, deterministically.

This runs right after Stage 1, before derived fields and before anything is
stored. Two reasons it is code and data rather than prompt text:

1. **The model's vocabulary is its own.** Left alone, one CV yields
   `title_canonical: "sales_specialist"` and the next `"sales_representative"`
   for the same job. Predicates and comparisons need one vocabulary, not a
   fluent improvisation per document.
2. **It improves without re-parsing.** Adding "Saha Satış Sorumlusu" to a
   family is a data edit; profiles can be re-normalized from stored extractions
   with no model call at all. Prompt changes cannot be applied retroactively —
   this can.

Every change is recorded, so the UI can show *why* a candidate matched
("'Kıdemli Kurumsal Satış Uzmanı' → kurumsal satış, kıdemli") and a mistake in
the tables is visible rather than mysterious.
"""

from dataclasses import dataclass, field

from senthire.domain.profile import ExtractedProfile
from senthire.normalize import education, geo, industry, languages, titles
from senthire.normalize.tables import version_signature


@dataclass
class NormalizationReport:
    version: str
    changes: list[dict] = field(default_factory=list)

    def record(self, path: str, name: str, before, after, via: str) -> None:
        if before == after:
            return
        self.changes.append(
            {"path": path, "field": name, "from": before, "to": after, "via": via}
        )

    def declined(self, path: str, name: str, saw, via: str) -> None:
        """Something was recognized and deliberately not applied.

        Recorded even though nothing changed: "we read this and chose to ignore
        it" is exactly the kind of decision a recruiter should be able to see,
        and a silent non-action is indistinguishable from a gap in the tables.
        """
        self.changes.append(
            {"path": path, "field": name, "from": saw, "to": None, "via": via, "declined": True}
        )

    @property
    def filled(self) -> int:
        return sum(1 for c in self.changes if c["from"] in (None, ""))

    @property
    def corrected(self) -> int:
        return len(self.changes) - self.filled

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "filled": self.filled,
            "corrected": self.corrected,
            "changes": self.changes,
        }


def normalize_profile(
    profile: ExtractedProfile, *, raw_text: str | None = None
) -> tuple[ExtractedProfile, NormalizationReport]:
    """Return a normalized copy of `profile` and the record of what changed."""
    report = NormalizationReport(version=version_signature())
    out = profile.model_copy(deep=True)

    _normalize_location(out, raw_text, report)
    _normalize_experience(out, report)
    _normalize_education(out, report)
    _normalize_languages(out, report)

    sectors = [e.industry_canonical for e in out.experience if e.industry_canonical]
    if sectors:
        merged = sorted(dict.fromkeys(sectors))
        report.record("industries", "industries", out.industries, merged, "industry-table")
        out.industries = merged
    return out, report


def _normalize_location(out: ExtractedProfile, raw_text: str | None, report) -> None:
    location = out.location
    source = location.raw or location.city_canonical
    # Aydın, Van, Kars, Bolu, Ordu, Rize and Sinop are provinces *and* common
    # Turkish surnames. If the only thing a "location" says is the candidate's
    # own name, resolving it would move Kerem Aydın to Aydın — a mistake that
    # reads as data rather than as an error, and one an extractor makes easily
    # because the name is the first line of every CV.
    if _is_person_name(source, out.identity.full_name):
        report.declined("location", "city_canonical", source, "geo:name-collision")
        location.city_canonical = None
        source = None
    match = geo.resolve(source)
    if match.province:
        # A correction, not just a fill: extractors routinely put the district
        # ("Çankaya") in city_canonical, which no requirement ever compares to.
        report.record(
            "location", "city_canonical", location.city_canonical, match.province,
            f"geo:{match.via}",
        )
        location.city_canonical = match.province
        if location.country is None:
            location.country = "TR"
    if location.relocation_signal.value is None:
        signal = geo.relocation_signal(raw_text or location.raw)
        if signal is not None:
            report.record("location", "relocation_signal", None, signal, "geo:phrase")
            location.relocation_signal.value = signal
            location.relocation_signal.info_status = "explicit"


def _is_person_name(value: str | None, full_name: str | None) -> bool:
    """True when the "location" is really (part of) the candidate's own name."""
    if not value or not full_name:
        return False
    from senthire.normalize.text import tokens

    value_tokens = tokens(value)
    name_tokens = tokens(full_name)
    return bool(value_tokens) and all(token in name_tokens for token in value_tokens)


def _normalize_experience(out: ExtractedProfile, report) -> None:
    for index, entry in enumerate(out.experience):
        path = f"experience[{index}]"
        match = titles.classify(entry.title_raw)
        if match.family:
            report.record(
                path, "title_canonical", entry.title_canonical, match.family,
                f"titles:{match.matched_alias}",
            )
            entry.title_canonical = match.family
        sector = industry.sector(entry.industry_canonical, entry.company, entry.description_summary)
        if sector:
            report.record(path, "industry_canonical", entry.industry_canonical, sector, "industry")
            entry.industry_canonical = sector


def _normalize_education(out: ExtractedProfile, report) -> None:
    for index, entry in enumerate(out.education):
        path = f"education[{index}]"
        match = education.classify(
            degree_raw=entry.field_raw if entry.degree is None else entry.degree,
            institution=entry.institution,
            field=entry.field_canonical or entry.field_raw,
        )
        if entry.degree is None and match.degree:
            report.record(path, "degree", None, match.degree, "education:degree")
            entry.degree = match.degree
        if match.institution:
            report.record(path, "institution", entry.institution, match.institution, "education:institution")
            entry.institution = match.institution
        if match.field:
            report.record(path, "field_canonical", entry.field_canonical, match.field, "education:field")
            entry.field_canonical = match.field


def _normalize_languages(out: ExtractedProfile, report) -> None:
    for index, entry in enumerate(out.languages):
        path = f"languages[{index}]"
        code = languages.language_code(entry.language)
        if code:
            report.record(path, "language", entry.language, code, "languages:code")
            entry.language = code
        if entry.cefr is None and entry.level_raw:
            match = languages.level(entry.level_raw)
            if match.cefr:
                report.record(path, "cefr", None, match.cefr, f"languages:{match.source}")
                entry.cefr = match.cefr
                if entry.info_status == "missing":
                    entry.info_status = "inferred"
