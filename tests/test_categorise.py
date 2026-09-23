"""
Tests for jobboard/categorise.py (§7). The spec asks for at least 40
example titles — this file has more than that, covering every category
in config/categories.yaml plus the AI-before-Data ordering rule and the
"Other" fallback.
"""
import pytest

from jobboard.categorise import Categoriser

TITLE_TO_EXPECTED_CATEGORY = [
    # Software Engineering
    ("Graduate Software Engineer", "Software Engineering"),
    ("Backend Developer", "Software Engineering"),
    ("Frontend Engineer", "Software Engineering"),
    ("Full Stack Developer", "Software Engineering"),
    ("iOS Engineer", "Software Engineering"),
    ("Android Developer", "Software Engineering"),
    ("DevOps Engineer", "Software Engineering"),
    ("Site Reliability Engineer", "Software Engineering"),
    ("QA Engineer", "Software Engineering"),
    ("Platform Engineer", "Software Engineering"),
    # AI / Machine Learning — must win over Data & Analytics for "Data Scientist"
    ("Data Scientist", "AI / Machine Learning"),
    ("Machine Learning Engineer", "AI / Machine Learning"),
    ("NLP Research Engineer", "AI / Machine Learning"),
    ("Computer Vision Engineer", "AI / Machine Learning"),
    ("AI Research Scientist", "AI / Machine Learning"),
    # Data & Analytics
    ("Data Analyst", "Data & Analytics"),
    ("Business Intelligence Analyst", "Data & Analytics"),
    ("Data Engineer", "Data & Analytics"),
    ("Insight Analyst", "Data & Analytics"),
    # Cyber Security
    ("Cyber Security Analyst", "Cyber Security"),
    ("Penetration Tester", "Cyber Security"),
    ("SOC Analyst", "Cyber Security"),
    # Product Management
    ("Product Manager", "Product Management"),
    ("Associate Product Manager", "Product Management"),
    # Design / UX
    ("UX Designer", "Design / UX"),
    ("Product Designer", "Design / UX"),
    ("Graphic Designer", "Design / UX"),
    # Finance & Accounting
    ("Financial Analyst", "Finance & Accounting"),
    ("Graduate Accountant", "Finance & Accounting"),
    ("Tax Analyst", "Finance & Accounting"),
    # Banking & Investment
    ("Investment Banking Analyst", "Banking & Investment"),
    ("Equity Research Analyst", "Banking & Investment"),
    ("Graduate Trader", "Banking & Investment"),
    # Consulting & Strategy
    ("Management Consultant", "Consulting & Strategy"),
    ("Strategy Analyst", "Consulting & Strategy"),
    # Marketing & Content
    ("Marketing Executive", "Marketing & Content"),
    ("Content Marketing Manager", "Marketing & Content"),
    ("SEO Specialist", "Marketing & Content"),
    # Sales & Business Development
    ("Sales Executive", "Sales & Business Development"),
    ("Business Development Representative", "Sales & Business Development"),
    ("Account Manager", "Sales & Business Development"),
    # Operations & Supply Chain
    ("Operations Analyst", "Operations & Supply Chain"),
    ("Supply Chain Graduate", "Operations & Supply Chain"),
    # HR & Recruitment
    ("HR Advisor", "HR & Recruitment"),
    ("Graduate Recruiter", "HR & Recruitment"),
    # Legal & Compliance
    ("Legal Counsel", "Legal & Compliance"),
    ("Compliance Analyst", "Legal & Compliance"),
    # Engineering (non-software)
    ("Mechanical Engineer", "Engineering (non-software)"),
    ("Electrical Engineer", "Engineering (non-software)"),
    ("Civil Engineer Graduate", "Engineering (non-software)"),
    # Healthcare & Science
    ("Clinical Research Associate", "Healthcare & Science"),
    ("Research Scientist", "Healthcare & Science"),
    # Customer Success & Support
    ("Customer Success Manager", "Customer Success & Support"),
    ("Technical Support Engineer", "Customer Success & Support"),
    # Other (fallback)
    ("Random Made Up Title Xyz", "Other"),
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
        # The spec explicitly asks for at least 40 example titles.
        assert len(TITLE_TO_EXPECTED_CATEGORY) >= 40

    def test_falls_back_to_description_when_title_matches_nothing(self, categoriser, make_job):
        job = make_job(
            title="Rising Star Programme",
            description_text="You'll be working as a machine learning engineer on our core models.",
        )
        assert categoriser.categorise(job) == "AI / Machine Learning"

    def test_falls_back_to_other_when_nothing_matches_at_all(self, categoriser, make_job):
        job = make_job(title="Rising Star Programme", description_text="Join our team and grow with us.")
        assert categoriser.categorise(job) == "Other"

    def test_first_match_wins_ai_before_data(self, categoriser, make_job):
        # A title that could plausibly match both AI/ML ("data scientist")
        # and Data & Analytics ("data analyst" is not present here, but
        # this checks the ordering intent directly) must land in AI/ML.
        job = make_job(title="Senior Data Scientist (Analytics team)")
        # "Senior" would normally be filtered out earlier in the pipeline
        # by filters.is_early_career — categorise.py itself doesn't care
        # about seniority, only about which keyword matches first.
        assert categoriser.categorise(job) == "AI / Machine Learning"
