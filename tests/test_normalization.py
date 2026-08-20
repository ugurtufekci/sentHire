"""The Turkish normalization layer: vocabularies, and the guards around them.

Two kinds of test here. The first is table-driven and boring on purpose — real
strings from real Turkish CVs, asserted against the canonical value the rest of
the pipeline compares. Those are the asset: every alias someone adds should
arrive with the line that proves it.

The second kind guards the tables themselves: an alias that maps to two
families answers differently depending on file order, and a protected
characteristic that sneaks into a vocabulary becomes scoreable. Both fail
silently in production, so they fail loudly here.
"""

import pytest

from senthire.domain.derived import compute_derived
from senthire.domain.profile import ExtractedProfile
from senthire.normalize import education, geo, industry, languages, text, titles
from senthire.normalize.profile import normalize_profile
from senthire.normalize.tables import table, version_signature

# --------------------------------------------------------------------------- #
# 1. Folding — the bug that looks like missing data
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("İSTANBUL", "istanbul"),
        ("İstanbul", "istanbul"),
        ("ISPARTA", "isparta"),
        ("Kıdemli", "kidemli"),
        ("Şişli / Beşiktaş", "sisli besiktas"),
        ("Yüksek Lisans, İşletme", "yuksek lisans isletme"),
        ("  çift   boşluk  ", "cift bosluk"),
    ],
)
def test_folding_is_turkish_aware(raw, expected):
    assert text.fold(raw) == expected


def test_dotted_and_dotless_i_collapse_to_the_same_key():
    # Python's str.lower() leaves "İ" as "i̇" (i + combining dot), so a naive
    # comparison silently fails on half the CVs in the country.
    assert text.fold("İZMİR") == text.fold("izmir") == text.fold("IZMIR")


def test_token_runs_do_not_match_inside_longer_words():
    assert text.contains_run(text.tokens("as gmbh"), ["as"])
    assert not text.contains_run(text.tokens("asistan"), ["as"])


# --------------------------------------------------------------------------- #
# 2. Titles: family and seniority are separate axes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "family", "seniority"),
    [
        ("Kurumsal Satış Uzmanı", "sales_corporate", None),
        ("Kıdemli Kurumsal Satış Uzmanı", "sales_corporate", "senior"),
        ("Satış Müdürü", "sales_generic", "manager"),
        ("Bölge Satış Müdürü", "sales_field", "manager"),
        ("Saha Satış Sorumlusu", "sales_field", None),
        ("Müşteri Temsilcisi", "sales_account", None),
        ("Key Account Manager", "sales_account", "manager"),
        ("Senior Account Executive", "sales_account", "senior"),
        ("Satış Danışmanı", "sales_retail", None),
        ("Tele Satış Temsilcisi", "sales_inside", None),
        ("İş Geliştirme Uzmanı", "business_development", None),
        ("Dijital Pazarlama Uzmanı", "marketing", None),
        ("Lojistik Operasyon Sorumlusu", "logistics", None),
        ("İthalat İhracat Uzmanı", "logistics", None),
        ("Satın Alma Uzman Yardımcısı", "purchasing", "junior"),
        ("Ön Muhasebe Elemanı", "finance_accounting", None),
        ("İnsan Kaynakları Uzmanı", "hr", None),
        ("Kıdemli Yazılım Geliştirici", "software_engineering", "senior"),
        ("Backend Developer", "software_engineering", None),
        ("Veri Analisti", "data", None),
        ("Sistem Yöneticisi", "devops", "manager"),
        ("Ürün Yöneticisi", "product", "manager"),
        ("Üretim Müdürü", "manufacturing", "manager"),
        ("Kalite Kontrol Sorumlusu", "quality_control", None),
        ("Endüstri Mühendisi", "engineering", None),
        ("Stajyer", None, "intern"),
        ("Genel Müdür", None, "executive_suite"),
    ],
)
def test_title_classification(raw, family, seniority):
    match = titles.classify(raw)
    assert (match.family, match.seniority) == (family, seniority)


def test_the_most_senior_marker_in_a_title_wins():
    # "Kıdemli ... Müdürü" is a manager, not a senior individual contributor.
    assert titles.classify("Kıdemli Satış Müdürü").seniority == "manager"


def test_seniority_words_do_not_hide_the_family():
    plain = titles.classify("Satış Uzmanı")
    senior = titles.classify("Kıdemli Satış Uzmanı")
    assert plain.family == senior.family == "sales_generic"


def test_an_unknown_title_is_unknown_not_guessed():
    assert titles.classify("Zihin Açıcı Baş Mimarı").family is None
    assert titles.classify(None).family is None


# --------------------------------------------------------------------------- #
# 3. Geography: the district problem
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "province"),
    [
        ("Ankara", "Ankara"),
        ("ANKARA", "Ankara"),
        ("Çankaya", "Ankara"),
        ("Ankara / Çankaya", "Ankara"),
        ("Ostim, Ankara", "Ankara"),
        ("Kadıköy / İstanbul", "İstanbul"),
        ("Kadikoy", "İstanbul"),
        ("Maslak", "İstanbul"),
        ("Gebze", "Kocaeli"),
        ("İzmit", "Kocaeli"),
        ("Bornova", "İzmir"),
        ("Nilüfer", "Bursa"),
        ("Çorlu", "Tekirdağ"),
        ("Afyon", "Afyonkarahisar"),
        ("İçel", "Mersin"),
        ("Urfa", "Şanlıurfa"),
        ("K.Maraş", "Kahramanmaraş"),
        ("Istanbul, Turkey", "İstanbul"),
    ],
)
def test_location_resolves_to_a_province(raw, province):
    assert geo.resolve(raw).province == province


def test_a_province_beats_a_district_in_the_same_string():
    # "Ankara Kadıköy" is nonsense data; answering Ankara is the safer read.
    assert geo.resolve("Ankara Kadıköy").province == "Ankara"


def test_unknown_locations_stay_unknown():
    assert geo.resolve("Uzak Diyarlar").province is None
    assert geo.resolve("").province is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Seyahat engeli yoktur", True),
        ("Şehir dışı görevlendirme yapabilirim", True),
        ("Open to relocation", True),
        ("Şehir dışına çıkamam", False),
        ("Ankara'da ikamet ediyorum", None),
        (None, None),
    ],
)
def test_relocation_signal_says_nothing_when_the_cv_says_nothing(raw, expected):
    assert geo.relocation_signal(raw) is expected


# --------------------------------------------------------------------------- #
# 4. Languages: prose and exam scores land on one axis
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "cefr"),
    [
        ("İyi derecede İngilizce", "B2"),
        ("Çok iyi", "C1"),
        ("İleri düzey", "C1"),
        ("Orta düzey", "B1"),
        ("Başlangıç seviyesi", "A2"),
        ("Ana dil", "native"),
        ("Akıcı", "C1"),
        ("B2 seviyesinde", "B2"),
        ("YDS: 78", "B2"),
        ("YDS 92", "C1"),
        ("YÖKDİL 61", "B1"),
        ("TOEFL IBT 100", "C1"),
        ("IELTS 6.5", "B2"),
        ("IELTS 7.5", "C1"),
        ("TOEIC 800", "B2"),
        ("CAE sertifikası", "C1"),
        ("FCE", "B2"),
    ],
)
def test_language_level_reading(raw, cefr):
    assert languages.level(raw).cefr == cefr


def test_exam_band_edges_are_inclusive():
    assert languages.level("YDS 75").cefr == "B2"
    assert languages.level("YDS 74").cefr == "B1"


def test_an_exam_score_beats_a_vague_phrase_in_the_same_string():
    # "iyi derecede İngilizce (YDS 92)" — the number is the harder evidence.
    assert languages.level("iyi derecede İngilizce YDS 92").cefr == "C1"


@pytest.mark.parametrize(
    ("raw", "code"),
    [("İngilizce", "en"), ("English", "en"), ("Almanca", "de"), ("Rusça", "ru"), ("Arapça", "ar")],
)
def test_language_names_map_to_iso_codes(raw, code):
    assert languages.language_code(raw) == code


# --------------------------------------------------------------------------- #
# 5. Education & industry
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "degree"),
    [
        ("Yüksek Lisans", "master"),
        ("Y. Lisans", "master"),
        ("MBA", "master"),
        ("Lisans", "bachelor"),
        ("Makine Mühendisliği", "bachelor"),
        ("Ön Lisans", "associate"),
        ("Meslek Yüksekokulu", "associate"),
        ("Anadolu Lisesi", "high_school"),
        ("Doktora", "doctorate"),
    ],
)
def test_degree_reading(raw, degree):
    assert education.classify(degree_raw=raw, institution=None, field=None).degree == degree


@pytest.mark.parametrize(
    ("raw", "institution"),
    [
        ("ODTÜ", "Orta Doğu Teknik Üniversitesi"),
        ("METU", "Orta Doğu Teknik Üniversitesi"),
        ("İTÜ", "İstanbul Teknik Üniversitesi"),
        ("Boğaziçi Üniversitesi", "Boğaziçi Üniversitesi"),
        ("AÖF", "Anadolu Üniversitesi"),
    ],
)
def test_institution_aliases(raw, institution):
    assert education.classify(degree_raw=None, institution=raw, field=None).institution == institution


@pytest.mark.parametrize(
    ("company", "sector"),
    [
        ("Örnek Lojistik San. ve Tic. A.Ş.", "logistics"),
        ("ABC Yazılım Ltd. Şti.", "software"),
        ("XYZ Bankası A.Ş.", "finance"),
        ("Deneme Tekstil Sanayi", "textile"),
        ("Falanca Otomotiv Holding", "automotive"),
        ("Bir İnşaat Taahhüt", "construction"),
    ],
)
def test_sector_from_company_name(company, sector):
    assert industry.sector(company) == sector


def test_legal_suffixes_come_off():
    assert industry.strip_legal_suffixes("Örnek Lojistik San. ve Tic. A.Ş.") == "ornek lojistik"


# --------------------------------------------------------------------------- #
# 6. The whole profile
# --------------------------------------------------------------------------- #


@pytest.fixture
def messy_profile() -> ExtractedProfile:
    return ExtractedProfile.model_validate(
        {
            "identity": {"full_name": "Test Aday"},
            "location": {"raw": "Çankaya / ANKARA"},
            "experience": [
                {
                    "title_raw": "Kıdemli Kurumsal Satış Uzmanı",
                    "title_canonical": "sales_specialist",  # the extractor's own vocabulary
                    "company": "Örnek Lojistik San. ve Tic. A.Ş.",
                    "start": "2019-01",
                    "is_current": True,
                }
            ],
            "education": [{"field_raw": "Yüksek Lisans, İşletme", "institution": "ODTÜ"}],
            "languages": [{"language": "İngilizce", "level_raw": "YDS: 78"}],
        }
    )


def test_normalization_fills_and_corrects_and_records_why(messy_profile):
    out, report = normalize_profile(messy_profile)

    assert out.location.city_canonical == "Ankara"
    assert out.experience[0].title_canonical == "sales_corporate"
    assert out.experience[0].industry_canonical == "logistics"
    assert out.education[0].degree == "master"
    assert out.education[0].institution == "Orta Doğu Teknik Üniversitesi"
    assert out.languages[0].language == "en"
    assert out.languages[0].cefr == "B2"
    assert out.industries == ["logistics"]

    # Every change is explainable in the UI, not a silent rewrite.
    city = next(c for c in report.changes if c["field"] == "city_canonical")
    assert city["from"] is None and city["to"] == "Ankara" and city["via"].startswith("geo:")
    title = next(c for c in report.changes if c["field"] == "title_canonical")
    assert title["from"] == "sales_specialist", "the extractor's guess is corrected, not kept"
    assert report.version == version_signature()


def test_normalization_is_idempotent(messy_profile):
    once, _ = normalize_profile(messy_profile)
    twice, second_report = normalize_profile(once)
    assert twice.model_dump() == once.model_dump()
    assert second_report.changes == []


def test_normalization_never_invents_a_location(messy_profile):
    messy_profile.location.raw = None
    messy_profile.location.city_canonical = None
    out, _ = normalize_profile(messy_profile)
    assert out.location.city_canonical is None, "missing must stay missing (docs/09)"


def test_a_stated_cefr_is_not_overwritten(messy_profile):
    messy_profile.languages[0].cefr = "C1"
    out, _ = normalize_profile(messy_profile)
    assert out.languages[0].cefr == "C1", "the CV's own explicit level outranks our inference"


def test_seniority_estimate_uses_the_title_taxonomy():
    def profile_with(title: str, start: str) -> ExtractedProfile:
        return ExtractedProfile.model_validate(
            {"experience": [{"title_raw": title, "start": start, "is_current": True}]}
        )

    from datetime import date

    today = date(2026, 8, 1)
    assert compute_derived(profile_with("Satış Müdürü", "2024-01"), today).seniority_estimate == "lead"
    assert compute_derived(profile_with("Kıdemli Satış Uzmanı", "2024-01"), today).seniority_estimate == "senior"
    assert compute_derived(profile_with("Satış Uzman Yardımcısı", "2025-06"), today).seniority_estimate == "junior"
    # An old lowercase-hint bug: "KIDEMLİ" in caps used to miss entirely.
    assert compute_derived(profile_with("KIDEMLİ SATIŞ UZMANI", "2024-01"), today).seniority_estimate == "senior"


# --------------------------------------------------------------------------- #
# 7. Guards on the tables themselves
# --------------------------------------------------------------------------- #


def _all_aliases() -> list[tuple[str, str, str]]:
    rows = []
    for family in table("titles")["families"]:
        rows += [("titles.family", alias, family["id"]) for alias in family["aliases"]]
    for level, aliases in table("titles")["seniority_markers"].items():
        rows += [("titles.seniority", alias, level) for alias in aliases]
    for cefr, phrases in table("languages")["phrases"].items():
        rows += [("languages.phrase", phrase, cefr) for phrase in phrases]
    for sector, keywords in table("industry")["sectors"].items():
        rows += [("industry.sector", keyword, sector) for keyword in keywords]
    for degree, phrases in table("education")["degrees"].items():
        rows += [("education.degree", phrase, degree) for phrase in phrases]
    return rows


def test_no_alias_maps_to_two_different_targets():
    """An ambiguous alias answers by file order, which is not an answer."""
    seen: dict[tuple[str, str], str] = {}
    clashes = []
    for scope, alias, target in _all_aliases():
        key = (scope, text.fold(alias))
        if key in seen and seen[key] != target:
            clashes.append(f"{scope}: '{alias}' → {seen[key]} and {target}")
        seen[key] = target
    assert not clashes, clashes


def test_aliases_are_stored_folded_enough_to_match():
    """A table entry that folding cannot reach is dead weight nobody notices."""
    dead = [
        f"{scope}: '{alias}'"
        for scope, alias, _ in _all_aliases()
        if not text.tokens(alias)
    ]
    assert not dead, dead


PROTECTED_VOCABULARY = [
    "askerlik", "askerlik durumu", "medeni hal", "medeni durum", "evli", "bekar",
    "cinsiyet", "kadin", "erkek", "yas", "dogum tarihi", "uyruk", "din", "mezhep",
    "engel durumu", "saglik durumu", "sendika", "siyasi", "hamile", "cocuk sahibi",
]


def test_no_vocabulary_can_make_a_protected_characteristic_scoreable():
    """docs/09's allowlist, enforced at the data layer.

    Military service is the Turkish-specific trap: it reads as a neutral CV
    field and is a gender proxy. If it ever became a canonical value, a spec
    could compare against it.
    """
    haystack = {text.fold(alias) for _scope, alias, _target in _all_aliases()}
    leaked = sorted(term for term in PROTECTED_VOCABULARY if text.fold(term) in haystack)
    assert not leaked, f"protected characteristics must not be normalizable: {leaked}"


def test_the_vocabulary_signature_covers_every_table():
    """Profiles are stamped with this. A table whose version is missing from the
    signature could change under stored profiles without anything noticing."""
    from senthire.normalize.tables import DATA_DIR

    signature = version_signature()
    for path in DATA_DIR.glob("*.json"):
        assert f"{path.stem}{table(path.stem)['version']}" in signature, path.stem
