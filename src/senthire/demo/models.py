"""Deterministic stand-ins that read the actual document, not a fixture.

These are not "return a canned profile" stubs. They parse the CV text, resolve
the city through the real geography table, read titles through the real title
taxonomy, and judge requirements by looking for the rubric's own words in the
profile. The output is therefore *plausible and specific to the input*, which
is what makes an offline run worth looking at — a demo where every candidate
scores the same teaches nobody anything.

What they are not is intelligent. They cannot weigh evidence, they cannot read
between lines, and they say so: every judgment they emit carries a reasoning
string that names the mechanism.
"""

import re
from datetime import date

from senthire.compiler.compiler import CompileResult
from senthire.domain import anchors
from senthire.domain.spec import (
    DeterministicCheck,
    EvaluationSpec,
    Requirement,
    SemanticCheck,
)
from senthire.normalize import geo, languages, titles
from senthire.normalize.text import fold, tokens
from senthire.screening.schemas import (
    DeepAnalysisOutput,
    EvidenceQuote,
    LightScreenOutput,
    ReqJudgment,
)

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?:\+90[\s-]?)?0?[\s-]?5\d{2}[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}")
YEAR_RANGE = re.compile(r"(19|20)\d{2}\s*[-–—]\s*((19|20)\d{2}|halen|devam|present|current)", re.I)
YEARS_WANTED = re.compile(r"(\d{1,2})\s*(?:\+\s*)?y[ıi]l")
# The linearizer inserts page markers; they are not CV content and they are
# exactly the shape ("=== Page 1 ===") a naive name heuristic mistakes for one.
PAGE_MARKER = re.compile(r"^=+\s*(page|sayfa)\s*\d+\s*=+$", re.I)

# Section-aware reading: a CV line means different things under different
# headers — "ODTÜ — İşletme (Lisans), 2012 - 2016" is education, not a job,
# and counting it as employment both invents experience months and hides the
# degree every "Lisans mezunu" gate needs.
SECTION_HEADERS = {
    "experience": ("deneyim", "is deneyimi", "calisma gecmisi", "profesyonel deneyim"),
    "education": ("egitim", "egitim bilgileri", "akademik gecmis", "ogrenim"),
    "language": ("dil", "diller", "yabanci dil", "yabanci diller"),
}

# Ordered: the longer phrase must win before its substring does.
DEGREE_LEVELS = (
    ("doktora", "doctorate"),
    ("yuksek lisans", "master"),
    ("on lisans", "associate"),
    ("onlisans", "associate"),
    ("lisans", "bachelor"),
    ("lise", "high_school"),
)


def _section_of(line: str) -> str | None:
    folded = fold(line).rstrip(":").strip()
    for name, keys in SECTION_HEADERS.items():
        if folded in keys:
            return name
    return None


def _degree_level(line: str) -> str | None:
    folded = fold(line)
    for key, level in DEGREE_LEVELS:
        if key in folded:
            return level
    return None


def _education_entry(line: str) -> dict:
    match = YEAR_RANGE.search(line)
    start_year = end_year = None
    if match:
        start_year = int(match.group(0)[:4])
        end_raw = match.group(2)
        end_year = int(end_raw) if end_raw[:2].isdigit() else None
    body = YEAR_RANGE.sub("", line).strip(" ,-–|")
    institution, _, rest = body.partition("—")
    field_raw = rest.split("(")[0].strip(" ,") or None
    return {
        "degree": _degree_level(line) or "other",
        "field_raw": field_raw,
        "institution": institution.strip(" ,") or None,
        "start_year": start_year,
        "end_year": end_year,
        "provenance": {"page": 1, "quote": line[:160]},
    }

STOPWORDS = {"ile", "ve", "veya", "olan", "bir", "için", "gibi", "daha", "çok", "en"}


# --------------------------------------------------------------------------- #
# Stage 1 — extraction
# --------------------------------------------------------------------------- #


COVER_LETTER_MARKERS = ("sayin", "saygilarimla", "basvurmak", "ilginiz icin", "iyi calismalar")


def _document_kind(text: str, lines: list[str]) -> str:
    """cv | cover_letter | other — decided from shape, not from wishful reading."""
    if len(text.strip()) < 40:
        return "other"  # no text layer: a scan or an image-only export
    folded = fold(text)
    has_dates = any(YEAR_RANGE.search(line) for line in lines)
    salutations = sum(1 for marker in COVER_LETTER_MARKERS if marker in folded)
    if not has_dates and salutations >= 2:
        return "cover_letter"
    return "cv"


def extract_pdf(data: bytes, *, escalated: bool = False):
    from senthire.domain.profile import ExtractedProfile
    from senthire.extraction.extractor import ExtractionOutcome
    from senthire.extraction.pdf import analyze_pdf

    analysis = analyze_pdf(data)
    text = analysis.text or ""
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not PAGE_MARKER.match(line.strip())
    ]

    # Two honest answers before pretending to read a CV. Production escalates a
    # text-less PDF to the vision path; offline there is no such path, so the
    # document is classified rather than silently turned into an empty profile
    # that would then be screened as a weak candidate.
    kind = _document_kind(text, lines)
    if kind != "cv":
        return ExtractionOutcome(
            profile=ExtractedProfile.model_validate(
                {
                    "document_kind": kind,
                    "language": "tr",
                    "confidence": 0.4,
                    "warnings": [
                        "offline demo: taranmış belge okunamadı (vision yolu kapalı)"
                        if kind == "other"
                        else "offline demo: belge CV değil"
                    ],
                }
            ),
            raw_text=text,
            path="text",
            model="offline-demo",
            prompt_version="demo_v1",
            page_count=analysis.page_count,
            input_tokens=0,
            output_tokens=0,
        )

    emails = EMAIL.findall(text)
    name = next(
        (line for line in lines[:6] if 2 <= len(line.split()) <= 4 and not EMAIL.search(line)),
        lines[0] if lines else "Aday",
    )
    # Never read a place out of the name line: half of Turkey's provinces are
    # also surnames, and the name is always at the top of the page.
    location = geo.resolve("")
    for line in lines:
        if line == name or "@" in line or YEAR_RANGE.search(line):
            continue
        candidate = geo.resolve(line)
        if candidate.province is not None:
            location = candidate
            break

    experience = []
    education = []
    section = None
    for line in lines:
        header = _section_of(line)
        if header:
            section = header
            continue
        if not YEAR_RANGE.search(line):
            if section == "education" and _degree_level(line):
                education.append(_education_entry(line))
            continue
        # A dated line is education when the section says so — or, on a CV
        # without headers, when it names a degree level.
        if section == "education" or (section is None and _degree_level(line)):
            education.append(_education_entry(line))
            continue
        if section == "language":
            continue
        match = YEAR_RANGE.search(line)
        start = match.group(0).split("-")[0].strip()[:4]
        end_raw = match.group(2)
        is_current = not end_raw[:2].isdigit()
        title_part = YEAR_RANGE.sub("", line).strip(" ,-–|")
        experience.append(
            {
                "title_raw": title_part[:120] or "Belirtilmemiş",
                "company": None,
                "employment_type": "full_time",
                "start": f"{start}-01",
                "end": None if is_current else f"{end_raw}-12",
                "is_current": is_current,
                "provenance": {"page": 1, "quote": line[:160]},
            }
        )

    language_skills = []
    for line in lines:
        code = languages.language_code(line)
        if code and code != "tr":
            level = languages.level(line)
            language_skills.append(
                {
                    "language": code,
                    "level_raw": line[:60],
                    "cefr": level.cefr,
                    "info_status": "explicit" if level.cefr else "ambiguous",
                    "provenance": {"page": 1, "quote": line[:160]},
                }
            )

    profile = ExtractedProfile.model_validate(
        {
            "document_kind": "cv",
            "language": "tr",
            "identity": {
                "full_name": name,
                "emails": emails[:1],
                "phones": PHONE.findall(text)[:1],
            },
            "location": {
                "raw": location.district or location.province,
                "city_canonical": location.province,
                "country": "TR" if location.province else None,
            },
            "experience": experience,
            "education": education,
            "languages": language_skills,
            "skills": [],
            # Honest about itself: a heuristic reader is not a confident one.
            "confidence": 0.6,
            "warnings": ["offline demo extraction — heuristic, not a model"],
        }
    )
    return ExtractionOutcome(
        profile=profile,
        raw_text=text,
        path="text",
        model="offline-demo",
        prompt_version="demo_v1",
        page_count=analysis.page_count,
        input_tokens=0,
        output_tokens=0,
    )


# --------------------------------------------------------------------------- #
# Stage 2 — requirement compilation
# --------------------------------------------------------------------------- #


def compile_spec(template_spec, nl_text, *, version: int, locale: str = "tr") -> CompileResult:
    requirements: list[Requirement] = list(template_spec.requirements) if template_spec else []
    understood: list[str] = []
    sentences = [s.strip() for s in re.split(r"[.;\n]", nl_text or "") if s.strip()]

    for index, sentence in enumerate(sentences):
        folded = fold(sentence)
        province = geo.resolve(sentence).province
        years = YEARS_WANTED.search(folded)
        language = languages.language_code(sentence)

        if province:
            requirements.append(
                Requirement(
                    req_id=f"D{index}_location",
                    category="location",
                    label={"tr": f"{province}'da ikamet"},
                    type="hard" if "zorunlu" in folded or "sart" in folded else "scored",
                    importance="high",
                    evaluator="deterministic",
                    deterministic=DeterministicCheck(
                        predicate={"field": "location.city_canonical", "op": "==", "value": province}
                    ),
                )
            )
            understood.append(f"{province}'da ikamet ediyor olmak")
        elif years:
            months = int(years.group(1)) * 12
            requirements.append(
                Requirement(
                    req_id=f"D{index}_experience",
                    category="relevant_experience",
                    label={"tr": f"En az {years.group(1)} yıl deneyim"},
                    type="hard",
                    importance="critical",
                    evaluator="deterministic",
                    deterministic=DeterministicCheck(
                        predicate={
                            "field": "derived.total_experience_months",
                            "op": ">=",
                            "value": months,
                        },
                        borderline_tolerance=0.1,
                    ),
                )
            )
            understood.append(f"en az {years.group(1)} yıl toplam deneyim")
        elif language and language != "tr":
            requirements.append(
                Requirement(
                    req_id=f"D{index}_language",
                    category="language",
                    label={"tr": f"{sentence.strip()[:40]}"},
                    type="scored",
                    importance="medium",
                    evaluator="deterministic",
                    deterministic=DeterministicCheck(
                        predicate={
                            "field": f"languages['{language}'].cefr_rank",
                            "op": ">=",
                            "value": 4,
                        }
                    ),
                )
            )
            understood.append(f"{sentence.strip()[:60]}")
        else:
            keywords = [t for t in tokens(sentence) if len(t) > 3 and t not in STOPWORDS][:6]
            if not keywords:
                continue
            requirements.append(
                Requirement(
                    req_id=f"D{index}_semantic",
                    category="relevant_experience",
                    label={"tr": sentence.strip()[:60]},
                    type="scored",
                    importance="high",
                    evaluator="semantic",
                    semantic=SemanticCheck(
                        rubric=f"Adayın şu kritere uygunluğunu değerlendir: {sentence.strip()}",
                        anchors=[],
                    ),
                )
            )
            understood.append(sentence.strip()[:60])

    spec = EvaluationSpec(
        version=version,
        locale=locale,
        weights=template_spec.weights if template_spec else {"relevant_experience": 0.6,
                                                             "location": 0.25, "language": 0.15},
        requirements=requirements,
    )
    return CompileResult(
        spec=spec,
        back_translation={
            "tr": "Anladığımız: " + "; ".join(understood) + ".",
            "en": "Understood: " + "; ".join(understood) + ".",
        },
        clarifications=[],
        compliance_flags=[],
    )


# --------------------------------------------------------------------------- #
# Stages 4 & 5 — judgment
# --------------------------------------------------------------------------- #


def _keywords(rubric: str) -> list[str]:
    return [t for t in tokens(rubric) if len(t) > 3 and t not in STOPWORDS][:8]


def light_screen(spec: EvaluationSpec, profile: dict):
    from senthire.screening.llm import LlmUsage

    haystack = fold(str(profile))
    judgments = []
    for req in spec.requirements:
        if req.evaluator not in {"semantic", "hybrid"} or req.semantic is None:
            continue
        keywords = _keywords(req.semantic.rubric)
        hits = [k for k in keywords if k in haystack]
        share = len(hits) / max(1, len(keywords))
        rungs = sorted(anchors.rungs(req), reverse=True)
        if not hits:
            verdict, raw = "unknown", None
        elif share >= 0.6:
            verdict, raw = "met", rungs[0]
        elif share >= 0.3:
            verdict, raw = "partially_met", rungs[len(rungs) // 2]
        else:
            verdict, raw = "partially_met", rungs[-2] if len(rungs) > 1 else rungs[0]
        judgments.append(
            ReqJudgment(
                req_id=req.req_id,
                verdict=verdict,
                score=raw,
                confidence=0.55 if verdict != "unknown" else 0.3,
                info_status="explicit" if hits else "missing",
                evidence=[EvidenceQuote(quote=hits[0], page=1)] if hits else [],
                reasoning=(
                    "Çevrimdışı demo: kriter sözcükleri profilde arandı "
                    f"({len(hits)}/{len(keywords)} eşleşme). Gerçek değerlendirme değildir."
                ),
            )
        )
    titles_seen = [
        titles.classify(e.get("title_raw")).label
        for e in profile.get("experience", [])
        if e.get("title_raw")
    ]
    return (
        LightScreenOutput(
            judgments=judgments,
            strengths=[t for t in titles_seen if t][:2],
            weaknesses=[],
            red_flags=[],
        ),
        LlmUsage("offline-demo", 0, 0, 0, 0),
    )


def deep_analyze(spec: EvaluationSpec, profile: dict, raw_text: str, light_judgments: list[dict]):
    from senthire.screening.llm import LlmUsage

    output, _ = light_screen(spec, profile)
    return (
        DeepAnalysisOutput(
            judgments=output.judgments,
            corrections=[],
            strengths=output.strengths,
            summary="Çevrimdışı demo derin analizi — ön değerlendirme tekrarlandı.",
        ),
        LlmUsage("offline-demo", 0, 0, 0, 0),
    )


def today() -> date:
    return date.today()
