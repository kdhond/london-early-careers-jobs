"""
Tests for jobboard/dedupe.py (§6.4). These specifically cover the tricky
cases the spec calls out: the same job posted with a slightly different
title, the same company written differently, and genuinely different
roles at the same company that must NOT be merged.
"""
from jobboard.dedupe import dedupe_jobs, normalise_title_for_dedupe


class TestDedupeJobs:
    def test_same_job_across_four_sources_merges_into_one(self, make_job, settings):
        jobs = [
            make_job(
                title="Graduate Software Engineer",
                company="Monzo",
                source="greenhouse",
                description_text="full desc " * 30,
                description_is_full=True,
                city="London",
            ),
            make_job(
                title="Graduate Software Engineer (Hybrid)",
                company="Monzo Bank Ltd",  # same company, written differently
                source="linkedin",
                description_text="linkedin desc",
                description_is_full=True,
                city="London",
            ),
            make_job(
                title="Graduate Software Engineer - London",  # location suffix should be stripped
                company="Monzo",
                source="reed",
                description_text="reed snippet",
                city="London",
            ),
            make_job(
                title="Graduate Software Engineer",
                company="Monzo",
                source="adzuna",
                description_text="adzuna snippet",
                city="London",
            ),
        ]
        result = dedupe_jobs(jobs, settings)

        assert len(result) == 1
        merged = result[0]
        assert set(merged.sources) == {"greenhouse", "linkedin", "reed", "adzuna"}
        assert len(merged.apply_links) == 4
        # Company career page (greenhouse) should win over linkedin/reed/adzuna for details.
        assert merged.description_is_full is True
        assert merged.description_text == "full desc " * 30

    def test_genuinely_different_roles_at_same_company_are_not_merged(self, make_job, settings):
        # This is the spec's explicit "must not merge" example.
        jobs = [
            make_job(
                title="Graduate Software Engineer – Backend",
                company="Monzo",
                source="greenhouse",
                description_text="backend role",
                city="London",
            ),
            make_job(
                title="Graduate Software Engineer – iOS",
                company="Monzo",
                source="greenhouse",
                description_text="ios role",
                city="London",
            ),
        ]
        result = dedupe_jobs(jobs, settings)
        assert len(result) == 2
        assert {job.title for job in result} == {
            "Graduate Software Engineer – Backend",
            "Graduate Software Engineer – iOS",
        }

    def test_different_roles_at_a_similarly_named_company_are_not_merged(self, make_job, settings):
        jobs = [
            make_job(title="Data Analyst", company="Wise", source="greenhouse", description_text="a", city="London"),
            make_job(title="Software Engineer", company="Wise plc", source="lever", description_text="b", city="London"),
        ]
        result = dedupe_jobs(jobs, settings)
        assert len(result) == 2

    def test_same_role_same_company_different_city_is_not_merged(self, make_job, settings):
        jobs = [
            make_job(title="Graduate Analyst", company="Deloitte", source="greenhouse", city="London"),
            make_job(title="Graduate Analyst", company="Deloitte", source="lever", city="Manchester"),
        ]
        result = dedupe_jobs(jobs, settings)
        assert len(result) == 2

    def test_earliest_posted_at_is_kept(self, make_job, settings):
        jobs = [
            make_job(title="Analyst", company="Acme", source="greenhouse", city="London", posted_at="2026-05-10"),
            make_job(title="Analyst", company="Acme", source="linkedin", city="London", posted_at="2026-05-01"),
        ]
        result = dedupe_jobs(jobs, settings)
        assert len(result) == 1
        assert result[0].posted_at == "2026-05-01"

    def test_empty_list_returns_empty_list(self, settings):
        assert dedupe_jobs([], settings) == []

    def test_single_job_is_returned_unchanged(self, make_job, settings):
        job = make_job(title="Analyst", company="Acme")
        result = dedupe_jobs([job], settings)
        assert result == [job]


class TestNormaliseTitleForDedupe:
    def test_strips_bracketed_content(self):
        assert normalise_title_for_dedupe("Software Engineer (Hybrid)") == "software engineer"

    def test_strips_known_location_suffix(self):
        assert normalise_title_for_dedupe("Software Engineer - London") == "software engineer"

    def test_does_not_strip_a_distinguishing_suffix(self):
        # "Backend" isn't a location/mode word, so it must be preserved —
        # this is what keeps "- Backend" and "- iOS" roles distinct.
        result = normalise_title_for_dedupe("Software Engineer - Backend")
        assert "backend" in result

    def test_strips_reference_codes(self):
        assert normalise_title_for_dedupe("Graduate Analyst REQ12345") == "graduate analyst"
