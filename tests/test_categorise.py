"""
Tests for jobboard/categorise.py: the general job-TYPE taxonomy
(config/categories.yaml) shown to every visitor of the public site —
Software Engineering, Data & Analytics, Finance & Accounting, etc.

This is a different, separate concept from jobboard/relevance.py's personal
"is this relevant to Kailas" flag (see test_relevance.py) — a job gets both
a `category` from here AND an `is_relevant` flag from there.
"""
import pytest

from jobboard.categorise import Categoriser

TITLE_TO_EXPECTED_CATEGORY = [
    # Software Engineering
    ("Graduate Software Engineer", "Software Engineering"),
    ("Backend Developer", "Software Engineering"),
    ("iOS Engineer", "Software Engineering"),
    ("DevOps Engineer", "Software Engineering"),
    ("QA Engineer", "Software Engineering"),
    # AI / Machine Learning
    ("Machine Learning Engineer", "AI / Machine Learning"),
    ("Data Scientist", "AI / Machine Learning"),
    ("NLP Research Engineer", "AI / Machine Learning"),
    # Data & Analytics
    ("Data Analyst", "Data & Analytics"),
    ("Business Intelligence Analyst", "Data & Analytics"),
    ("Data Engineer", "Data & Analytics"),
    # Cyber Security
    ("Cyber Security Analyst", "Cyber Security"),
    ("Penetration Tester", "Cyber Security"),
    # Product Management
    ("Associate Product Manager", "Product Management"),
    ("Product Owner", "Product Management"),
    # Design / UX
    ("UX Designer", "Design / UX"),
    ("Product Designer", "Design / UX"),
    # Finance & Accounting
    ("Graduate Accountant", "Finance & Accounting"),
    ("Tax Analyst", "Finance & Accounting"),
    ("FP&A Analyst", "Finance & Accounting"),
    # Banking & Investment
    ("Investment Banking Analyst", "Banking & Investment"),
    ("Graduate Trader", "Banking & Investment"),
    ("Equity Research Analyst", "Banking & Investment"),
    # Consulting & Strategy — specific phrases only, see below for the
    # deliberately-excluded bare "consultant"/"consulting" cases.
    ("Management Consultant", "Consulting & Strategy"),
    ("Strategy Analyst", "Consulting & Strategy"),
    # Marketing & Content
    ("Marketing Executive", "Marketing & Content"),
    ("Content Marketing Manager", "Marketing & Content"),
    # Sales & Business Development
    ("Sales Executive", "Sales & Business Development"),
    ("Business Development Representative", "Sales & Business Development"),
    # Operations & Supply Chain
    ("Operations Analyst", "Operations & Supply Chain"),
    ("Supply Chain Analyst", "Operations & Supply Chain"),
    # HR & Recruitment
    ("HR Advisor", "HR & Recruitment"),
    ("Talent Acquisition Partner", "HR & Recruitment"),
    # Legal & Compliance
    ("Legal Counsel", "Legal & Compliance"),
    ("Compliance Analyst", "Legal & Compliance"),
    # Engineering (non-software)
    ("Mechanical Engineer", "Engineering (non-software)"),
    ("Civil Engineer", "Engineering (non-software)"),
    # Healthcare & Science
    ("Graduate Pharmacist", "Healthcare & Science"),
    ("Clinical Research Associate", "Healthcare & Science"),
    ("Research Scientist", "Healthcare & Science"),
    # Customer Success & Support
    ("Customer Success Manager", "Customer Success & Support"),
    ("Technical Support Engineer", "Customer Success & Support"),
    # Other — everything not in the above
    ("Random Made Up Title Xyz", "Other"),
    # "Consultant" alone is deliberately not a Consulting & Strategy keyword
    # (see relevance.yaml's header for why — recruitment agencies use
    # "Consultant" as a generic sales job title). For this GENERAL type
    # taxonomy (unlike the personal relevance list) that's fine: these
    # titles still correctly land in a real category via their own other
    # words ("recruitment" -> HR & Recruitment, "sales" -> Sales & Business
    # Development) — they just don't get miscategorised as "Consulting."
    ("Recruitment Consultant", "HR & Recruitment"),
    ("Luxury Sales Consultant", "Sales & Business Development"),
]


@pytest.fixture(scope="module")
def categoriser():
    return Categoriser()


class TestCategorise:
    @pytest.mark.parametrize("title,expected_category", TITLE_TO_EXPECTED_CATEGORY)
    def test_title_matches_expected_category(self, categoriser, make_job, title, expected_category):
        job = make_job(title=title)
        assert categoriser.categorise(job) == expected_category

    def test_at_least_forty_titles_are_covered(self):
        assert len(TITLE_TO_EXPECTED_CATEGORY) >= 40

    def test_falls_back_to_other_when_nothing_matches_at_all(self, categoriser, make_job):
        job = make_job(title="Rising Star Programme", description_text="Join our team and grow with us.")
        assert categoriser.categorise(job) == "Other"

    def test_description_is_used_as_a_fallback_when_title_matches_nothing(self, categoriser, make_job):
        # Unlike relevance.yaml (title-only, deliberately), this general
        # taxonomy DOES fall back to the description when the title alone
        # doesn't match anything — see categorise.py.
        job = make_job(
            title="Rising Star Programme",
            description_text="You'll be working as a machine learning engineer on our core models.",
        )
        assert categoriser.categorise(job) == "AI / Machine Learning"

    def test_ai_ml_checked_before_data_analytics_for_data_scientist(self, categoriser, make_job):
        # categories.yaml's ordering note: "Data Scientist" must land in
        # AI/ML, not get caught by Data & Analytics' "data analyst"-style
        # keywords, because AI/ML is checked first.
        job = make_job(title="Data Scientist")
        assert categoriser.categorise(job) == "AI / Machine Learning"
