"""
Tests for jobboard/relevance.py: Kailas's personal biotech/VC/health-equity/
finance "is this relevant to me" flag (config/relevance.yaml) — moved out of
test_categorise.py 2026-09-25 when the public site got a general job-TYPE
taxonomy back (see test_categorise.py) and this became a separate, personal
concept. Matching logic is unchanged from before; only the interface changed
(a boolean `is_relevant`, not a category string).
"""
import pytest

from jobboard.relevance import RelevanceChecker

TITLE_TO_EXPECTED_RELEVANCE = [
    # Health equity / public health
    ("Health Equity Analyst", True),
    ("Public Health Associate", True),
    ("Maternal Health Program Manager", True),
    ("Community Health Coordinator", True),
    ("Population Health Analyst", True),
    # Venture capital — bio/health/pharma-qualified only (2026-09-30 rework)
    ("Biotech Venture Associate", True),
    ("Healthcare VC Analyst", True),
    ("Life Sciences Venture Partner", True),
    ("Digital Health Venture Capital Analyst", True),
    # Equity research / investment banking — bio/health/pharma-qualified only
    ("Biotech Equity Analyst", True),
    ("Healthcare Analyst, Investment Bank", True),
    ("Biotech Equity Research Analyst", True),
    ("Healthcare Investment Banking Analyst", True),
    ("Pharma Equity Research Associate", True),
    # Biotech / health AI
    ("Biotech AI Research Scientist", True),
    ("Digital Health Product Analyst", True),
    ("Computational Biology Associate", True),
    ("Bioinformatics Engineer", True),
    ("Drug Discovery Data Scientist", True),
    ("Genomics AI Intern", True),
    # Biotech, health & life sciences
    ("Clinical Research Associate", True),
    ("Biotech Research Scientist", True),
    ("Life Sciences Graduate Programme", True),
    ("Regulatory Affairs Associate", True),
    ("Pharmacovigilance Officer", True),
    ("Graduate Pharmacist", True),
    # Biology-specific titles — added 2026-09-30, "more emphasis on biology
    # and life sciences roles that could use some AI but aren't AI primarily."
    ("Biologist", True),
    ("Cell Biology Research Assistant", True),
    ("Epidemiology Analyst", True),
    ("Preclinical Scientist", True),
    ("Medical Science Liaison", True),
    ("Translational Research Associate", True),
    ("Biostatistics Graduate", True),
    ("Toxicology Associate", True),
    # Consulting & strategy
    ("Management Consultant", True),
    ("Strategy Analyst", True),
    # Not relevant — everything not in the above
    ("Graduate Software Engineer", False),
    ("Product Manager", False),
    ("UX Designer", False),
    ("Marketing Executive", False),
    ("Sales Executive", False),
    ("Operations Analyst", False),
    ("HR Advisor", False),
    ("Legal Counsel", False),
    ("Mechanical Engineer", False),
    ("Customer Success Manager", False),
    ("Cyber Security Analyst", False),
    ("Random Made Up Title Xyz", False),
    # Recruitment agencies use "Consultant" as a generic sales job title —
    # confirmed live in production polluting "Relevant" with these — so the
    # bare word "consultant"/"consulting" is deliberately not a keyword.
    ("Recruitment Consultant", False),
    ("Luxury Sales Consultant", False),
    ("Assistant Acoustic Consultant", False),
    ("Operational Technology Consultant", False),
    # Generic finance/VC/accounting — removed 2026-09-30, no longer relevant
    # without a biotech/healthcare/pharma/life-sciences qualifier in the
    # title (bare "Investment Analyst"/"Venture Associate" matched ANY
    # sector, not just biotech).
    ("Venture Capital Analyst", False),
    ("Venture Associate", False),
    ("VC Analyst", False),
    ("Venture Partner", False),
    ("Equity Research Analyst", False),
    ("Investment Banking Analyst", False),
    ("Graduate Trader", False),
    ("Quantitative Analyst", False),
    ("Financial Analyst", False),
    ("Graduate Accountant", False),
    ("Tax Analyst", False),
    ("FP&A Analyst", False),
    # Generic AI/ML/data — removed 2026-09-30, "ml and ai engineering roles
    # i dont really qualify for": real-world example was IQVIA's "Agentic
    # AI, Data Engineer COA Accelerator", a pure data-engineering build role
    # that isn't a biology one, sneaking into Relevant via bare "ai"/"data
    # engineer" keywords.
    ("Data Scientist", False),
    ("Machine Learning Engineer", False),
    ("Data Analyst", False),
    ("Business Intelligence Analyst", False),
    ("NLP Research Engineer", False),
    ("Agentic AI, Data Engineer COA Accelerator", False),
]


@pytest.fixture(scope="module")
def checker():
    return RelevanceChecker()


class TestRelevance:
    @pytest.mark.parametrize("title,expected", TITLE_TO_EXPECTED_RELEVANCE)
    def test_title_matches_expected_relevance(self, checker, make_job, title, expected):
        job = make_job(title=title)
        assert checker.is_relevant(job) is expected

    def test_at_least_forty_titles_are_covered(self):
        assert len(TITLE_TO_EXPECTED_RELEVANCE) >= 40

    def test_falls_back_to_not_relevant_when_nothing_matches_at_all(self, checker, make_job):
        job = make_job(title="Rising Star Programme", description_text="Join our team and grow with us.")
        assert checker.is_relevant(job) is False

    def test_description_text_is_not_matched_only_title(self, checker, make_job):
        # Matching is title-only (see relevance.yaml's header) precisely
        # because full descriptions are full of unrelated boilerplate that
        # happens to contain a keyword — confirmed live in production with
        # things like "M&E Quantity Surveyor" (UK construction jargon: "M&E"
        # = Mechanical, Electrical and Public Health) and "Team Assistant"
        # (an unrelated startup's About blurb saying "backed by venture
        # capital") both getting mislabeled as relevant. A vague title with
        # a relevant-sounding description must NOT be swept in.
        job = make_job(
            title="Rising Star Programme",
            description_text="You'll be working as a machine learning engineer on our core models.",
        )
        assert checker.is_relevant(job) is False


class TestGeneralistRoleHintTiebreaker:
    """
    "Founder's Associate" / "Chief of Staff" / "Entrepreneur in Residence"
    are sector-agnostic titles that keyword-matching alone can't place —
    relevance.py resolves them using the company's category_hint instead
    (only set on Greenhouse/Lever/Ashby jobs, via config/companies.yaml).
    """

    def test_founders_associate_at_biotech_ai_company_is_relevant(self, checker, make_job):
        job = make_job(title="Founder's Associate", category_hint="biotech-ai")
        assert checker.is_relevant(job) is True

    def test_chief_of_staff_at_health_equity_company_is_relevant(self, checker, make_job):
        job = make_job(title="Chief of Staff", category_hint="health-equity")
        assert checker.is_relevant(job) is True

    def test_founders_associate_at_biotech_vc_company_is_relevant(self, checker, make_job):
        job = make_job(title="Founders Associate", category_hint="biotech-vc")
        assert checker.is_relevant(job) is True

    def test_founders_associate_with_no_hint_falls_back_to_not_relevant(self, checker, make_job):
        # Reed/Adzuna/LinkedIn jobs never have a category_hint (only
        # Greenhouse/Lever/Ashby jobs do) — without one, a generalist title
        # with no other keyword signal in it just isn't relevant.
        job = make_job(title="Founder's Associate", category_hint=None)
        assert checker.is_relevant(job) is False

    def test_founders_associate_at_unrelated_company_hint_is_not_forced_relevant(
        self, checker, make_job
    ):
        job = make_job(title="Founder's Associate", category_hint="fintech")
        assert checker.is_relevant(job) is False


class TestHintFallbackForNonGeneralistTitles:
    """
    category_hint is curated, verified data (every hinted company in
    config/companies.yaml was hand-checked), unlike free-text description
    matching — so it's also used as a final fallback for jobs at a known
    biotech/VC/health-equity company whose title/description didn't trip
    any keyword rule at all, instead of leaving them not-relevant.
    """

    def test_generic_role_at_biotech_ai_company_is_still_relevant(self, checker, make_job):
        job = make_job(title="Application Support Engineer", category_hint="biotech-ai")
        assert checker.is_relevant(job) is True

    def test_generic_role_with_no_hint_is_not_relevant(self, checker, make_job):
        job = make_job(title="Application Support Engineer", category_hint=None)
        assert checker.is_relevant(job) is False

    def test_keyword_match_still_wins_over_hint_fallback(self, checker, make_job):
        # A title keyword match should still take priority over the hint
        # fallback when both are available — the hint is a fallback, not an
        # override.
        job = make_job(title="Management Consultant", category_hint="biotech-ai")
        assert checker.is_relevant(job) is True


class TestEngineeringRolesExcludedFromHintFallback:
    """
    Added 2026-09-25: "i dont want software engineering jobs as relevant to
    me." The company-hint fallback above is deliberately narrow — it should
    NOT rescue a software/hardware engineering title just because the
    company happens to be a verified biotech/VC/health-equity one.
    """

    def test_software_engineer_at_biotech_ai_company_is_not_relevant(self, checker, make_job):
        job = make_job(title="Software Engineer", category_hint="biotech-ai")
        assert checker.is_relevant(job) is False

    def test_backend_engineer_at_biotech_ai_company_is_not_relevant(self, checker, make_job):
        job = make_job(title="Backend Engineer", category_hint="biotech-ai")
        assert checker.is_relevant(job) is False

    def test_ios_engineer_at_health_equity_company_is_not_relevant(self, checker, make_job):
        job = make_job(title="iOS Engineer", category_hint="health-equity")
        assert checker.is_relevant(job) is False

    def test_engineering_generalist_title_at_biotech_company_is_not_relevant(self, checker, make_job):
        # The generalist-title tiebreaker (Founder's Associate etc.) also
        # respects the engineering exclusion, though a "Software Engineer,
        # Founder's Associate"-style title is unlikely in practice — this
        # just confirms the exclusion check runs before that tiebreaker too.
        job = make_job(title="Software Engineer / Founder's Associate", category_hint="biotech-ai")
        assert checker.is_relevant(job) is False

    def test_non_engineering_role_at_same_company_is_still_relevant(self, checker, make_job):
        # Sanity check: the exclusion is specific to engineering titles, not
        # a general regression of the hint fallback.
        job = make_job(title="Office Manager", category_hint="biotech-ai")
        assert checker.is_relevant(job) is True

    def test_data_engineer_at_biotech_ai_company_is_not_relevant(self, checker, make_job):
        # Added 2026-09-30: Kailas has a biomedical sciences background, not
        # an ML/AI engineering one — a data/ML/AI engineering build role
        # isn't relevant even at a verified biotech company.
        job = make_job(title="Data Engineer", category_hint="biotech-ai")
        assert checker.is_relevant(job) is False

    def test_ml_engineer_at_health_equity_company_is_not_relevant(self, checker, make_job):
        job = make_job(title="Machine Learning Engineer", category_hint="health-equity")
        assert checker.is_relevant(job) is False


class TestGenericFinanceTitleStillCaughtByHintFallback:
    """
    2026-09-30: bare finance/VC titles (e.g. "Venture Associate") no longer
    match any keyword on their own — they need a biotech/healthcare/pharma
    qualifier in the title. But a generic finance/VC title at a company
    we've already verified as biotech-VC/biotech-ai/health-equity should
    still come through, via the same hint fallback as any other generic
    role there (see TestHintFallbackForNonGeneralistTitles).
    """

    def test_venture_associate_at_biotech_vc_company_is_relevant(self, checker, make_job):
        job = make_job(title="Venture Associate", category_hint="biotech-vc")
        assert checker.is_relevant(job) is True

    def test_venture_associate_with_no_hint_is_not_relevant(self, checker, make_job):
        job = make_job(title="Venture Associate", category_hint=None)
        assert checker.is_relevant(job) is False
