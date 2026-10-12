"""Tests for the generic own-careers-page reader (no network: fake HTTP)."""
import httpx

from jobboard.sources.careers_page import CareersPageSource

LISTING = """<html><body>
<a href="/careers/research-scientist">Research Scientist</a>
<a href="/careers/benefits">Benefits</a>
<a href="/about">About us at the company</a>
<a href="/careers">Careers</a>
</body></html>"""

AD = ("<html><body><h1>Research Scientist</h1><p>Cambridge. About the role: lab work. "
      "Responsibilities include assays. Requirements: a biology degree. "
      + "Filler text. " * 100 + "</p></body></html>")
LD = """<html><script type="application/ld+json">{"@type":"JobPosting","title":"Lab Technician",
"datePosted":"2026-10-01","description":"<p>Do lab things</p>",
"jobLocation":{"address":{"addressLocality":"London","addressCountry":"GB"}}}</script></html>"""


def _run(pages):
    def handler(request):
        body = pages.get(request.url.path)
        return httpx.Response(200 if body else 404, text=body or "", headers={"content-type": "text/html"})
    src = CareersPageSource.__new__(CareersPageSource)
    src.companies = []
    company = {"name": "Acme Bio", "careers_url": "https://acme.test/careers"}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return src._fetch_company(client, company)


def test_reads_posting_links_and_skips_nav():
    jobs = _run({"/careers": LISTING, "/careers/research-scientist": AD, "/careers/benefits": AD})
    assert [j.title for j in jobs] == ["Research Scientist"]
    assert "cambridge" in jobs[0].location.lower()


def test_prefers_json_ld():
    jobs = _run({"/careers": LISTING, "/careers/research-scientist": LD})
    assert jobs[0].title == "Lab Technician" and jobs[0].location == "London, GB"
    assert jobs[0].posted_at == "2026-10-01"


def test_non_job_page_is_rejected():
    assert _run({"/careers": LISTING, "/careers/research-scientist": "<html><h1>Hi</h1>hello</html>"}) == []
