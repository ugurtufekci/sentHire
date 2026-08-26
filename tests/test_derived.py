from datetime import date

from senthire.domain.derived import compute_derived, months_inclusive, parse_ym
from senthire.domain.profile import EducationEntry, ExperienceEntry, ExtractedProfile

TODAY = date(2026, 8, 1)


def exp(**kwargs) -> ExperienceEntry:
    return ExperienceEntry(title_raw=kwargs.pop("title", "Satış Uzmanı"), **kwargs)


def profile(*entries: ExperienceEntry, education=()) -> ExtractedProfile:
    return ExtractedProfile(experience=list(entries), education=list(education))


def test_parse_ym_variants():
    assert parse_ym("2019-03") == date(2019, 3, 1)
    assert parse_ym("2019") == date(2019, 1, 1)
    assert parse_ym("03/2019") == date(2019, 3, 1)
    assert parse_ym("2019-03-15") == date(2019, 3, 1)
    assert parse_ym("Halen") is None
    assert parse_ym(None) is None
    assert parse_ym("garbage") is None


def test_months_inclusive():
    assert months_inclusive(date(2019, 3, 1), date(2019, 3, 1)) == 1
    assert months_inclusive(date(2020, 1, 1), date(2020, 12, 1)) == 12


def test_total_experience_merges_overlaps():
    d = compute_derived(
        profile(
            exp(company="A", start="2020-01", end="2020-12"),
            exp(company="B", start="2020-06", end="2021-06"),  # overlaps A
        ),
        today=TODAY,
    )
    # merged interval 2020-01..2021-06 = 18 months, not 12 + 13
    assert d.total_experience_months == 18
    assert d.job_count == 2


def test_current_role_counts_to_today():
    d = compute_derived(profile(exp(company="A", start="2026-01", is_current=True)), today=TODAY)
    assert d.total_experience_months == 8  # 2026-01..2026-08 inclusive
    assert d.current_employment_status == "employed"


def test_unknown_end_excluded_from_totals_but_counted_as_job():
    d = compute_derived(profile(exp(company="A", start="2020-01")), today=TODAY)
    assert d.total_experience_months == 0
    assert d.job_count == 1
    assert d.current_employment_status == "unemployed"


def test_gap_detection():
    d = compute_derived(
        profile(
            exp(company="A", start="2018-01", end="2018-06"),
            exp(company="B", start="2019-01", end="2020-01"),
        ),
        today=TODAY,
    )
    assert len(d.employment_gaps) == 1
    gap = d.employment_gaps[0]
    assert gap.months == 6  # 2018-07..2018-12
    assert gap.from_month == "2018-07"
    assert gap.to_month == "2019-01"
    assert d.max_employment_gap_months == 6


def test_short_gap_ignored():
    d = compute_derived(
        profile(
            exp(company="A", start="2018-01", end="2018-06"),
            exp(company="B", start="2018-09", end="2019-01"),  # 2-month gap < threshold
        ),
        today=TODAY,
    )
    assert d.employment_gaps == []


def test_job_changes_last_5y_counts_only_employer_switches_in_window():
    d = compute_derived(
        profile(
            exp(company="A", start="2015-01", end="2017-01"),
            exp(company="B", start="2017-02", end="2022-06"),  # switch before window (window starts 2021-08)
            exp(company="C", start="2022-07", end="2024-01"),  # in window
            exp(company="C", start="2024-02", end="2025-01"),  # same employer → not a change
            exp(company="D", start="2025-02", is_current=True),  # in window
        ),
        today=TODAY,
    )
    assert d.job_changes_last_5y == 2


def test_degree_rank_and_seniority():
    d = compute_derived(
        profile(
            exp(title="Kıdemli Satış Uzmanı", company="A", start="2018-01", is_current=True),
            education=[EducationEntry(degree="master")],
        ),
        today=TODAY,
    )
    assert d.highest_degree_rank == 4
    assert d.seniority_estimate == "senior"


def test_unstated_education_is_none_not_zero():
    """Missing != negative: an empty education list must read as "the document
    does not say", which predicates turn into unknown — never into a failed
    "Lisans mezunu" gate. (Caught by the 220-CV rehearsal: every synthetic CV
    was rejected on a rank the extractor had folded to 0.)"""
    from senthire.domain.derived import compute_derived
    from senthire.domain.predicates import evaluate
    from senthire.domain.profile import ExtractedProfile

    profile = ExtractedProfile.model_validate({"document_kind": "cv", "language": "tr"})
    derived = compute_derived(profile)
    assert derived.highest_degree_rank is None

    doc = {"derived": {"highest_degree_rank": None}}
    verdict = evaluate(
        {"field": "education.highest_degree_rank", "op": ">=", "value": 3}, doc
    )
    assert verdict == "unknown"


def test_stated_education_keeps_its_rank():
    from senthire.domain.derived import compute_derived
    from senthire.domain.profile import ExtractedProfile

    profile = ExtractedProfile.model_validate(
        {
            "document_kind": "cv",
            "language": "tr",
            "education": [{"degree": "high_school"}],
        }
    )
    assert compute_derived(profile).highest_degree_rank == 1
