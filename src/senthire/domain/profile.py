"""Candidate profile schemas (docs/03 §3).

`ExtractedProfile` is exactly what the extraction model fills via structured
outputs — no derived math, no protected attributes (the schema is the allowlist:
there are deliberately no fields for age, gender, religion, ethnicity, photo,
marital status, nationality). `DerivedFields` is computed in code
(senthire.domain.derived). The stored document composes both.

Structured-output constraints honored here: no recursive models, no numeric/
string constraints, `extra="forbid"` everywhere (=> additionalProperties:false).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

PROFILE_SCHEMA_VERSION = "1.0"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Provenance(_Strict):
    page: int | None = None
    quote: str | None = None


class Link(_Strict):
    type: str | None = None  # linkedin | github | portfolio | other
    url: str


class Identity(_Strict):
    full_name: str | None = None
    emails: list[str] = []
    phones: list[str] = []
    links: list[Link] = []


class RelocationSignal(_Strict):
    value: bool | None = None
    info_status: Literal["explicit", "inferred", "ambiguous", "missing"] = "missing"


class Location(_Strict):
    raw: str | None = None
    city_canonical: str | None = None
    country: str | None = None  # ISO 3166-1 alpha-2
    relocation_signal: RelocationSignal = RelocationSignal()


class ExperienceSignals(_Strict):
    b2b: bool | None = None
    b2c: bool | None = None
    quota_carrying: bool | None = None
    people_managed: int | None = None
    crm_tools: list[str] = []


class ExperienceEntry(_Strict):
    title_raw: str
    title_canonical: str | None = None
    company: str | None = None
    industry_canonical: str | None = None
    employment_type: (
        Literal["full_time", "part_time", "contract", "internship", "freelance", "unknown"]
    ) = "unknown"
    start: str | None = None  # "YYYY-MM" or "YYYY"
    end: str | None = None  # "YYYY-MM" / "YYYY" / None when current or unstated
    is_current: bool = False
    description_summary: str | None = None
    signals: ExperienceSignals = ExperienceSignals()
    provenance: Provenance = Provenance()


class EducationEntry(_Strict):
    degree: Literal["high_school", "associate", "bachelor", "master", "doctorate", "other"] | None = (
        None
    )
    field_raw: str | None = None
    field_canonical: str | None = None
    institution: str | None = None
    start_year: int | None = None
    end_year: int | None = None
    provenance: Provenance = Provenance()


class Skill(_Strict):
    name_raw: str
    canonical: str | None = None
    evidence: Literal["listed", "demonstrated"] | None = None
    provenance: Provenance = Provenance()


CEFR = Literal["A1", "A2", "B1", "B2", "C1", "C2", "native"]


class LanguageSkill(_Strict):
    language: str  # ISO 639-1 code, e.g. "en", "tr", "de"
    level_raw: str | None = None
    cefr: CEFR | None = None
    info_status: Literal["explicit", "inferred", "ambiguous", "missing"] = "explicit"
    provenance: Provenance = Provenance()


class Certification(_Strict):
    name: str
    name_canonical: str | None = None
    issuer: str | None = None
    year: int | None = None
    provenance: Provenance = Provenance()


class CareerTransition(_Strict):
    from_area: str
    to_area: str
    year: int | None = None


class Career(_Strict):
    transitions: list[CareerTransition] = []
    summary: str | None = None  # 2–3 sentences, labeled AI-generated in the UI


class ExtractedProfile(_Strict):
    document_kind: Literal["cv", "cover_letter", "transcript", "job_description", "other"] = "cv"
    language: str | None = None  # dominant source language, ISO 639-1
    multi_person: bool = False  # True if the document seems to contain >1 person's CV
    identity: Identity = Identity()
    location: Location = Location()
    experience: list[ExperienceEntry] = []
    education: list[EducationEntry] = []
    skills: list[Skill] = []
    languages: list[LanguageSkill] = []
    certifications: list[Certification] = []
    industries: list[str] = []
    tools_technologies: list[str] = []
    career: Career = Career()
    confidence: float | None = None  # extractor's own 0–1 estimate
    warnings: list[str] = []
    full_text: str | None = None  # filled only on the vision path (transcription)


class EmploymentGap(_Strict):
    from_month: str  # "YYYY-MM"
    to_month: str
    months: int


class DerivedFields(_Strict):
    """Computed in code — never by the LLM (docs/02 Stage 1.5)."""

    total_experience_months: int = 0
    job_count: int = 0
    avg_tenure_months: float | None = None
    job_changes_last_5y: int = 0
    employment_gaps: list[EmploymentGap] = []
    max_employment_gap_months: int = 0
    current_employment_status: Literal["employed", "unemployed", "unknown"] = "unknown"
    highest_degree_rank: int = 0  # 0 none … 5 doctorate
    seniority_estimate: Literal["junior", "mid", "senior", "lead", "unknown"] = "unknown"


DEGREE_RANK: dict[str, int] = {
    "high_school": 1,
    "associate": 2,
    "bachelor": 3,
    "master": 4,
    "doctorate": 5,
    "other": 1,
}

CEFR_RANK: dict[str, int] = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6, "native": 6}


def compose_profile_document(
    extracted: ExtractedProfile,
    derived: DerivedFields,
    *,
    model: str,
    prompt_version: str,
    path: str,
    confidence: float | None,
) -> dict:
    """The JSONB document stored in candidate_profiles.profile (docs/03 §3)."""
    doc = extracted.model_dump(exclude={"full_text"})
    doc["schema_version"] = PROFILE_SCHEMA_VERSION
    doc["derived"] = derived.model_dump()
    doc["extraction"] = {
        "model": model,
        "prompt_version": prompt_version,
        "path": path,
        "confidence": confidence,
        "warnings": extracted.warnings,
    }
    return doc
