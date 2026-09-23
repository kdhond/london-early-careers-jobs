"""
Tests for jobboard/extract.py, using realistic sample descriptions rather
than toy strings — the spec explicitly asks for this ("Unit-test each of
these with realistic sample descriptions").
"""
from jobboard.extract import (
    detect_work_mode,
    extract_requirements,
    extract_salary,
    extract_years_required,
)

REALISTIC_HTML_DESCRIPTION = """
<strong>Who We Are: <br><br></strong>
At RED, we're shaping the future of the built environment.
<br><br>
<strong>What You'll Need: <br><br></strong>
<ul>
<li>Minimum requirement of BSc/BEng (or equivalent) in a relevant engineering discipline</li>
<li>Working towards Chartered Engineer status</li>
<li>Basic knowledge of BIM and engineering software</li>
<li>Strong IT skills (MS Office, spreadsheets, calculations)</li>
</ul>
<strong>Benefits at RED:<br><br></strong>
<ul>
<li>23 days annual leave, rising to 28 days with service</li>
<li>Hybrid working</li>
</ul>
"""

NO_HEADING_DESCRIPTION = """
<p>We are looking for someone with 3 years of experience and a degree in
computer science. Must be proficient in Python and knowledge of Docker
is a bonus.</p>
"""


class TestExtractRequirements:
    def test_collects_bullets_under_a_matching_heading(self):
        result = extract_requirements(REALISTIC_HTML_DESCRIPTION)
        assert result == [
            "Minimum requirement of BSc/BEng (or equivalent) in a relevant engineering discipline",
            "Working towards Chartered Engineer status",
            "Basic knowledge of BIM and engineering software",
            "Strong IT skills (MS Office, spreadsheets, calculations)",
        ]

    def test_stops_at_an_unrelated_heading(self):
        result = extract_requirements(REALISTIC_HTML_DESCRIPTION)
        assert "23 days annual leave, rising to 28 days with service" not in result
        assert "Hybrid working" not in result

    def test_bare_word_in_a_bullet_is_not_mistaken_for_a_heading(self):
        # "Strong IT skills" contains the word "skills", which is also one
        # of our heading phrases — it must still be collected as content,
        # not treated as a new section heading (see extract.py's
        # _is_heading, anchored to match the *whole* line).
        result = extract_requirements(REALISTIC_HTML_DESCRIPTION)
        assert "Strong IT skills (MS Office, spreadsheets, calculations)" in result

    def test_falls_back_to_keyword_lines_when_no_heading_found(self):
        result = extract_requirements(NO_HEADING_DESCRIPTION)
        assert len(result) >= 1
        assert any("experience" in item.lower() for item in result)

    def test_caps_at_twelve_items(self):
        many_bullets = "<strong>Requirements</strong>" + "".join(
            f"<li>Requirement number {i}</li>" for i in range(20)
        )
        result = extract_requirements(many_bullets)
        assert len(result) == 12

    def test_empty_description_returns_empty_list(self):
        assert extract_requirements("") == []


class TestExtractYearsRequired:
    def test_plus_years(self):
        assert extract_years_required("3+ years of experience required") == 3

    def test_range_uses_lower_bound(self):
        assert extract_years_required("You will have 2-4 years experience in a similar role") == 2

    def test_at_least(self):
        assert extract_years_required("at least 5 years of relevant experience") == 5

    def test_minimum_of(self):
        assert extract_years_required("minimum of 6 years in software") == 6

    def test_word_number(self):
        assert extract_years_required("candidates with five years experience preferred") == 5

    def test_yrs_abbreviation(self):
        assert extract_years_required("5 yrs experience needed") == 5

    def test_no_mention_returns_none(self):
        assert extract_years_required("A great opportunity for a recent graduate") is None

    def test_empty_string_returns_none(self):
        assert extract_years_required("") is None


class TestExtractSalary:
    def test_range_with_k_suffix(self):
        result = extract_salary("£30k-£35k per annum")
        assert result.min == 30000.0
        assert result.max == 35000.0

    def test_range_with_commas_and_to(self):
        result = extract_salary("£45,000 to £50,000")
        assert result.min == 45000.0
        assert result.max == 50000.0

    def test_single_amount_pro_rata(self):
        result = extract_salary("£35,000 pro rata")
        assert result.min == 35000.0
        assert result.max == 35000.0

    def test_single_amount_with_k_suffix(self):
        result = extract_salary("Salary: £40k")
        assert result.min == 40000.0

    def test_no_salary_mentioned_returns_none(self):
        assert extract_salary("A great opportunity for a recent graduate") is None

    def test_empty_string_returns_none(self):
        assert extract_salary("") is None


class TestDetectWorkMode:
    def test_hybrid_wins_even_if_remote_also_mentioned(self):
        text = "This is a hybrid role (not fully remote), 3 days in the office"
        assert detect_work_mode(text) == "hybrid"

    def test_remote(self):
        assert detect_work_mode("Fully remote, work from anywhere in the UK") == "remote"

    def test_work_from_home_counts_as_remote(self):
        assert detect_work_mode("This role offers WFH flexibility") == "remote"

    def test_onsite(self):
        assert detect_work_mode("This is an on-site role in our London office") == "onsite"

    def test_unknown_when_nothing_mentioned(self):
        assert detect_work_mode("A great opportunity for a recent graduate") == "unknown"

    def test_empty_string_is_unknown(self):
        assert detect_work_mode("") == "unknown"
