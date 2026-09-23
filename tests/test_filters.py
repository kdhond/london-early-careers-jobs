"""Tests for jobboard/filters.py: the 0-4 years experience filter and the England location filter (§6)."""
from jobboard.filters import is_early_career, is_in_england, normalise_city


class TestIsEarlyCareer:
    def test_graduate_title_passes(self, make_job, settings):
        job = make_job(title="Graduate Software Engineer")
        assert is_early_career(job, settings) is True

    def test_senior_title_is_excluded(self, make_job, settings):
        job = make_job(title="Senior Software Engineer")
        assert is_early_career(job, settings) is False

    def test_lead_title_is_excluded(self, make_job, settings):
        job = make_job(title="Team Lead")
        assert is_early_career(job, settings) is False

    def test_head_of_phrase_is_excluded(self, make_job, settings):
        job = make_job(title="Head of Engineering")
        assert is_early_career(job, settings) is False

    def test_architect_is_not_excluded_by_default(self, make_job, settings):
        # The spec explicitly calls this out: "solutions architect" roles
        # can be junior, so "architect" is deliberately not in the default
        # excluded_title_words list.
        job = make_job(title="Solutions Architect")
        assert is_early_career(job, settings) is True

    def test_high_years_required_excludes(self, make_job, settings):
        job = make_job(title="Software Engineer", description_text="Requires 6+ years experience")
        assert is_early_career(job, settings) is False

    def test_low_years_required_passes(self, make_job, settings):
        job = make_job(title="Software Engineer", description_text="Requires 3+ years experience")
        assert is_early_career(job, settings) is True

    def test_no_years_mentioned_passes(self, make_job, settings):
        job = make_job(title="Software Engineer", description_text="Join our growing team")
        assert is_early_career(job, settings) is True

    def test_mid_senior_seniority_field_excludes_even_with_junior_title(self, make_job, settings):
        job = make_job(title="Data Analyst", seniority="Mid-Senior level")
        assert is_early_career(job, settings) is False

    def test_entry_level_seniority_field_passes(self, make_job, settings):
        job = make_job(title="Junior Data Analyst", seniority="Entry level")
        assert is_early_career(job, settings) is True

    def test_whole_word_match_does_not_false_positive(self, make_job, settings):
        # "Leadership Development Programme" should NOT be excluded by the
        # "lead" keyword — \b word boundaries prevent that.
        job = make_job(title="Leadership Development Programme")
        assert is_early_career(job, settings) is True


class TestIsInEngland:
    def test_london_matches(self, make_job, settings):
        job = make_job(location="London, England, United Kingdom")
        assert is_in_england(job, settings) is True

    def test_other_england_city_matches(self, make_job, settings):
        job = make_job(location="Manchester, UK")
        assert is_in_england(job, settings) is True

    def test_remote_uk_matches(self, make_job, settings):
        job = make_job(location="Remote, UK")
        assert is_in_england(job, settings) is True

    def test_scotland_is_excluded(self, make_job, settings):
        job = make_job(location="Glasgow, United Kingdom")
        assert is_in_england(job, settings) is False

    def test_wales_is_excluded(self, make_job, settings):
        job = make_job(location="Cardiff, Wales")
        assert is_in_england(job, settings) is False

    def test_northern_ireland_is_excluded(self, make_job, settings):
        job = make_job(location="Belfast, Northern Ireland")
        assert is_in_england(job, settings) is False

    def test_outside_uk_is_excluded(self, make_job, settings):
        job = make_job(location="New York, NY, United States")
        assert is_in_england(job, settings) is False

    def test_new_york_does_not_false_positive_on_the_city_york(self, make_job, settings):
        # Regression test: "New York" contains the substring "York" (an
        # England city in settings.yaml), which a naive substring match
        # would wrongly treat as a match.
        job = make_job(location="New York, NY, United States")
        assert is_in_england(job, settings) is False

    def test_actual_york_matches(self, make_job, settings):
        job = make_job(location="York, United Kingdom")
        assert is_in_england(job, settings) is True

    def test_empty_location_is_excluded(self, make_job, settings):
        job = make_job(location="")
        assert is_in_england(job, settings) is False


class TestNormaliseCity:
    def test_extracts_known_city(self, settings):
        assert normalise_city("London, England, United Kingdom", settings) == "London"

    def test_remote_becomes_remote_uk(self, settings):
        assert normalise_city("Remote, UK", settings) == "Remote (UK)"

    def test_unknown_location_falls_back_to_first_segment(self, settings):
        assert normalise_city("Somewhere Else, England", settings) == "Somewhere Else"

    def test_empty_location_returns_empty_string(self, settings):
        assert normalise_city("", settings) == ""
