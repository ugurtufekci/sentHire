"""The offline demo extractor must read a CV the way the sections mean it:
an education line is a degree, not a job. This is the layer every offline
demo and browser audit stands on — it earned unit tests the day a template's
"Lisans mezunu" gate rejected 220 of 220 demo candidates because education
was never parsed at all (and the same dated line was counted as employment).
"""

import pymupdf

from senthire.demo.models import extract_pdf
from senthire.domain.profile import DEGREE_RANK

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def pdf_bytes(lines: list[str]) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    y = 60
    for line in lines:
        page.insert_text((50, y), line, fontsize=10.5, fontfile=FONT, fontname="dejavu")
        y += 16
    data = doc.tobytes()
    doc.close()
    return data


STANDARD_CV = [
    "AYŞE YILMAZ",
    "ayse.yilmaz@ornekmail.com | +90 532 111 22 33",
    "Çankaya / Ankara",
    "",
    "DENEYİM",
    "Kıdemli Satış Uzmanı — Aksa Teknoloji A.Ş.   2019 - halen",
    "  Kurumsal müşterilere B2B satış.",
    "Satış Temsilcisi — Beta Yazılım Ltd.   2016 - 2019",
    "",
    "EĞİTİM",
    "Orta Doğu Teknik Üniversitesi — İşletme (Lisans), 2012 - 2016",
    "",
    "DİL",
    "İngilizce: İyi derecede (YDS: 82)",
]


def test_education_is_parsed_not_counted_as_employment():
    outcome = extract_pdf(pdf_bytes(STANDARD_CV))
    profile = outcome.profile

    assert len(profile.education) == 1, "the EĞİTİM line must become a degree"
    entry = profile.education[0]
    assert entry.degree == "bachelor"
    assert DEGREE_RANK[entry.degree] >= 3, 'a "Lisans mezunu" hard gate must be satisfiable'
    assert entry.institution and "Teknik" in entry.institution
    assert entry.field_raw and "İşletme" in entry.field_raw
    assert (entry.start_year, entry.end_year) == (2012, 2016)

    titles = [e.title_raw for e in profile.experience]
    assert len(titles) == 2, f"the degree line must not inflate employment: {titles}"
    assert all("Üniversite" not in t for t in titles)


def test_degree_levels_resolve_in_the_right_order():
    for line, expected in [
        ("Bilkent Üniversitesi — İşletme (Yüksek Lisans), 2016 - 2018", "master"),
        ("Ankara Üniversitesi — Hukuk (Doktora), 2015 - 2020", "doctorate"),
        ("Anadolu Üniversitesi — Muhasebe (Ön Lisans), 2010 - 2012", "associate"),
        ("Atatürk Lisesi, 2004 - 2008", "high_school"),
    ]:
        cv = ["TEST ADAY", "test@ornek.com", "", "EĞİTİM", line]
        outcome = extract_pdf(pdf_bytes(cv))
        assert [e.degree for e in outcome.profile.education] == [expected], line


def test_headerless_cv_still_finds_the_degree():
    cv = [
        "MEHMET DEMİR",
        "mehmet.demir@ornek.com",
        "Satış Uzmanı — Vega Bilişim   2018 - halen",
        "Ege Üniversitesi — İktisat (Lisans), 2013 - 2017",
    ]
    outcome = extract_pdf(pdf_bytes(cv))
    profile = outcome.profile
    assert [e.degree for e in profile.education] == ["bachelor"]
    assert len(profile.experience) == 1


def test_language_section_lines_never_become_jobs():
    cv = [
        "ELİF KAYA",
        "elif@ornek.com",
        "",
        "DENEYİM",
        "Uzman — Nova A.Ş.   2020 - halen",
        "",
        "DİL",
        "İngilizce kursu 2019 - 2020 sertifikalı",
    ]
    outcome = extract_pdf(pdf_bytes(cv))
    assert len(outcome.profile.experience) == 1
