"""Tests for the Workable / Recruitee / SmartRecruiters / Personio sources (HTTP is mocked)."""
import httpx

from jobboard.sources import more_ats

COMPANY = {"name": "Acme Bio", "token": "acme", "website": "https://acme.bio", "category_hint": "biotech-ai"}


def _fake_requests(monkeypatch, routes):
    """Replace request_with_retry with a lookup of {url-substring: json-or-bytes}."""
    def fake(client, method, url, **kwargs):
        for needle, body in routes.items():
            if needle in url:
                if isinstance(body, bytes):
                    return httpx.Response(200, content=body)
                return httpx.Response(200, json=body)
        return httpx.Response(404)
    monkeypatch.setattr(more_ats, "request_with_retry", fake)
    monkeypatch.setattr(more_ats.time, "sleep", lambda s: None)


def _fetch(source_cls):
    source = source_cls()
    source.companies = [COMPANY]
    return source.fetch()


def test_workable(monkeypatch):
    _fake_requests(monkeypatch, {"widget/accounts/acme": {"jobs": [{
        "title": "Research Associate", "city": "London", "state": "", "country": "United Kingdom",
        "employment_type": "Full-time", "published_on": "2026-10-01",
        "description": "<p>Do <b>science</b></p>", "url": "https://apply.workable.com/j/ABC",
    }]}})
    [job] = _fetch(more_ats.WorkableSource)
    assert job.location == "London, United Kingdom"
    assert job.posted_at == "2026-10-01"
    assert job.sources == ["workable"] and job.category_hint == "biotech-ai"
    assert "science" in job.description_text and "<" not in job.description_text


def test_recruitee(monkeypatch):
    _fake_requests(monkeypatch, {"recruitee.com/api/offers": {"offers": [{
        "title": "Analyst", "location": "London, England, United Kingdom",
        "description": "<p>Desc</p>", "requirements": "<ul><li>Reqs</li></ul>",
        "published_at": "2026-10-02 10:00:00 UTC", "careers_url": "https://acme.recruitee.com/o/analyst",
    }]}})
    [job] = _fetch(more_ats.RecruiteeSource)
    assert job.posted_at == "2026-10-02"
    assert "Desc" in job.description_text and "Reqs" in job.description_text


def test_smartrecruiters_fetches_detail(monkeypatch):
    _fake_requests(monkeypatch, {
        "postings/99": {"postingUrl": "https://jobs.smartrecruiters.com/Acme/99",
                        "jobAd": {"sections": {"jobDescription": {"title": "Job", "text": "<p>Hello</p>"}}}},
        "postings?": {"content": [{"id": "99", "name": "Scientist", "releasedDate": "2026-09-29T13:19:02.150Z",
                                    "location": {"fullLocation": "Cambridge, England, United Kingdom"}}]},
    })
    [job] = _fetch(more_ats.SmartRecruitersSource)
    assert job.location == "Cambridge, England, United Kingdom"
    assert job.apply_links[0].url == "https://jobs.smartrecruiters.com/Acme/99"
    assert "Hello" in job.description_text


def test_personio_includes_additional_offices(monkeypatch):
    xml = b"""<workzag-jobs><position><id>7</id><office>Berlin</office>
      <additionalOffices><office>London</office></additionalOffices><name>Associate</name>
      <jobDescriptions><jobDescription><name>Role</name><value><![CDATA[<p>Hi</p>]]></value></jobDescription></jobDescriptions>
      <employmentType>permanent</employmentType><createdAt>2026-10-03T08:00:00+00:00</createdAt></position></workzag-jobs>"""
    _fake_requests(monkeypatch, {"personio.de/xml": xml})
    [job] = _fetch(more_ats.PersonioSource)
    assert "London" in job.location  # second office still lets it pass the London filter
    assert job.apply_links[0].url == "https://acme.jobs.personio.de/job/7"


def test_missing_board_is_skipped(monkeypatch):
    _fake_requests(monkeypatch, {})
    assert _fetch(more_ats.WorkableSource) == []


def test_workday_pages_to_total_and_uses_detail_location(monkeypatch):
    """Workday wraps around past the end, so paging must stop at `total`; location comes from the detail call."""
    from jobboard.sources import workday

    calls = {"posts": 0}

    def fake(client, method, url, **kwargs):
        if method == "POST":
            calls["posts"] += 1
            # Always returns the same single page, like Workday looping back to the start.
            return httpx.Response(200, json={"total": 1, "jobPostings": [
                {"title": "Scientist", "externalPath": "/job/X_1", "locationsText": "2 Locations"}]})
        return httpx.Response(200, json={"jobPostingInfo": {
            "title": "Scientist", "location": "Stevenage", "additionalLocations": ["London, UK"],
            "startDate": "2026-10-05", "jobDescription": "<p>Lab</p>", "timeType": "Full time",
            "externalUrl": "https://gsk.wd5.myworkdayjobs.com/GSKCareers/job/X_1"}})

    monkeypatch.setattr(workday, "request_with_retry", fake)
    source = workday.WorkdaySource()
    source.companies = [{**COMPANY, "wd": "wd5", "site": "Careers"}]
    jobs = source.fetch()
    assert calls["posts"] == 1                      # stopped at total=1, no wrap-around loop
    assert len(jobs) == 1
    assert "London" in jobs[0].location             # additional office kept so it passes the location filter
    assert jobs[0].posted_at == "2026-10-05"
    assert jobs[0].sources == ["workday"]


def test_bamboohr_fetches_detail_and_appends_country(monkeypatch):
    _fake_requests(monkeypatch, {
        "careers/47/detail": {"result": {"jobOpening": {
            "jobOpeningName": "Computational Scientist", "employmentStatusLabel": "Full-Time",
            "location": {"city": "Abingdon", "state": "Oxfordshire"}, "atsLocation": {"country": None},
            "description": "<p>Cells</p>", "datePosted": "2026-08-04",
            "jobOpeningShareUrl": "https://acme.bamboohr.com/careers/47"}}},
        "careers/list": {"result": [{"id": "47", "jobOpeningName": "Computational Scientist"}]},
    })
    source = more_ats.BambooHRSource()
    source.companies = [{**COMPANY, "country": "United Kingdom"}]
    [job] = source.fetch()
    # "Abingdon, Oxfordshire" alone wouldn't pass the England filter; the configured country makes it explicit.
    assert job.location == "Abingdon, Oxfordshire, United Kingdom"
    assert job.posted_at == "2026-08-04" and "Cells" in job.description_text


def test_teamtailor_rss(monkeypatch):
    rss = b"""<?xml version="1.0"?><rss xmlns:tt="https://teamtailor.com/locations"><channel><item>
      <title>Clinical Project Manager</title><description>&lt;p&gt;Immunotherapy&lt;/p&gt;</description>
      <pubDate>Thu, 01 Oct 2026 13:10:26 +0100</pubDate><link>https://careers.acme.bio/jobs/1</link>
      <remoteStatus>hybrid</remoteStatus><tt:locations></tt:locations></item></channel></rss>"""
    _fake_requests(monkeypatch, {"teamtailor.com/jobs.rss": rss})
    source = more_ats.TeamtailorSource()
    source.companies = [{**COMPANY, "country": "United Kingdom"}]
    [job] = source.fetch()
    assert job.location == "United Kingdom"        # feed had no location, so the configured country is used
    assert job.posted_at == "2026-10-01" and job.work_mode == "hybrid"
    assert "Immunotherapy" in job.description_text and "<" not in job.description_text


def test_bamboohr_keeps_the_feeds_real_country(monkeypatch):
    """A Brazilian office must stay Brazilian even if the company has a configured home country."""
    detail = lambda city, state, country: {"result": {"jobOpening": {
        "jobOpeningName": "Production Technician", "location": {"city": city, "state": state, "addressCountry": country},
        "atsLocation": {}, "description": "<p>x</p>", "jobOpeningShareUrl": "https://acme.bamboohr.com/careers/1"}}}
    _fake_requests(monkeypatch, {"careers/1/detail": detail("Campinas - SP", "São Paulo", "Brazil"),
                                 "careers/list": {"result": [{"id": "1", "jobOpeningName": "x"}]}})
    source = more_ats.BambooHRSource(); source.companies = [{**COMPANY, "country": "United Kingdom"}]
    [job] = source.fetch()
    assert "Brazil" in job.location and "United Kingdom" not in job.location


def test_bamboohr_falls_back_to_configured_country_only_when_feed_has_none(monkeypatch):
    _fake_requests(monkeypatch, {"careers/1/detail": {"result": {"jobOpening": {
        "jobOpeningName": "Scientist", "location": {"city": "Abingdon", "state": "Oxfordshire"}, "atsLocation": {},
        "description": "<p>x</p>", "jobOpeningShareUrl": "https://acme.bamboohr.com/careers/1"}}},
        "careers/list": {"result": [{"id": "1", "jobOpeningName": "x"}]}})
    source = more_ats.BambooHRSource(); source.companies = [{**COMPANY, "country": "United Kingdom"}]
    [job] = source.fetch()
    assert job.location == "Abingdon, Oxfordshire, United Kingdom"
