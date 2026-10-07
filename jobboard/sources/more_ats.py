"""
Four more ATS (Applicant Tracking System) sources: Workable, Recruitee,
SmartRecruiters and Personio.

Like Greenhouse/Lever/Ashby, each of these platforms hosts careers pages for
lots of small companies and publishes a free, public, no-key-required feed of
each company's open jobs. They're popular with small UK biotechs and startups
that are too small for Greenhouse, which is why they're worth polling.

All four share the same shape, so they share a base class:
  - the companies to poll come from config/companies.yaml (rows whose `ats`
    matches the source's `name`)
  - `fetch()` loops over those companies and converts each posting to a `Job`
  - a company whose board is gone/renamed is skipped, never fatal

Docs:
  Workable        https://workable.readme.io/reference/jobs
  Recruitee       https://docs.recruitee.com/reference/offers
  SmartRecruiters https://developers.smartrecruiters.com/docs/posting-api
  Personio        https://developer.personio.de/docs/retrieving-open-job-positions
"""
from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET

import httpx

from jobboard.models import ApplyLink, Job
from jobboard.sources.base import USER_AGENT, Source, load_yaml, request_with_retry

_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(raw_html: str) -> str:
    """Unescape HTML entities then strip tags, leaving plain text for matching."""
    if not raw_html:
        return ""
    return _TAG_RE.sub(" ", html.unescape(raw_html)).strip()


def _date_prefix(value) -> str | None:
    """'2026-09-29T13:19:02Z' or '2026-09-29 13:19:02 UTC' -> '2026-09-29'."""
    return (value or "")[:10] or None


def _join_location(*parts) -> str:
    """Join the non-empty location parts, e.g. ('London', '', 'United Kingdom') -> 'London, United Kingdom'."""
    return ", ".join(p for p in parts if p)


class _CompanyATSSource(Source):
    """Shared plumbing: load this platform's companies from companies.yaml."""

    def __init__(self):
        # companies.yaml is shared by every ATS source; each keeps only its own rows.
        companies = load_yaml("companies.yaml") or []
        self.companies = [c for c in companies if c.get("ats") == self.name]

    def is_configured(self) -> bool:
        # No API key needed — runs as long as at least one company is configured.
        return len(self.companies) > 0

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        with httpx.Client(headers={"User-Agent": USER_AGENT}) as client:
            for company in self.companies:
                try:
                    jobs.extend(self._fetch_company(client, company))
                except Exception:
                    # One broken board must not lose the others.
                    continue
        return jobs

    def _fetch_company(self, client: httpx.Client, company: dict) -> list[Job]:
        raise NotImplementedError

    def _job(self, company: dict, **fields) -> Job:
        """Build a Job with the fields every ATS source sets the same way."""
        return Job.new(
            company=company["name"],
            source=self.name,
            company_website=company.get("website"),
            category_hint=company.get("category_hint"),
            description_is_full=True,
            **fields,
        )


class WorkableSource(_CompanyATSSource):
    name = "workable"

    def _fetch_company(self, client, company):
        # details=true includes each posting's full HTML description.
        url = f"https://apply.workable.com/api/v1/widget/accounts/{company['token']}?details=true"
        resp = request_with_retry(client, "GET", url)
        # Workable rate-limits bursts, so pause briefly between companies.
        time.sleep(0.5)
        if resp.status_code != 200:
            return []
        out = []
        for raw in resp.json().get("jobs", []):
            description_html = raw.get("description", "") or ""
            out.append(self._job(
                company,
                title=raw.get("title", ""),
                location=_join_location(raw.get("city"), raw.get("state"), raw.get("country")),
                employment_type=raw.get("employment_type"),
                posted_at=_date_prefix(raw.get("published_on") or raw.get("created_at")),
                description_html=description_html,
                description_text=_html_to_text(description_html),
                apply_links=[ApplyLink(source=self.name, url=raw.get("url") or raw.get("shortlink", ""))],
            ))
        return out


class RecruiteeSource(_CompanyATSSource):
    name = "recruitee"

    def _fetch_company(self, client, company):
        # Recruitee gives each company its own subdomain.
        resp = request_with_retry(client, "GET", f"https://{company['token']}.recruitee.com/api/offers/")
        if resp.status_code != 200:
            return []
        out = []
        for raw in resp.json().get("offers", []):
            # The "requirements" section is separate from the description;
            # join them so extract.py's heading-based parser can find both.
            description_html = (raw.get("description") or "") + (raw.get("requirements") or "")
            out.append(self._job(
                company,
                title=raw.get("title", ""),
                location=raw.get("location") or _join_location(raw.get("city"), raw.get("country")),
                employment_type=raw.get("employment_type_code"),
                posted_at=_date_prefix(raw.get("published_at") or raw.get("created_at")),
                description_html=description_html,
                description_text=_html_to_text(description_html),
                apply_links=[ApplyLink(source=self.name, url=raw.get("careers_url", ""))],
            ))
        return out


class SmartRecruitersSource(_CompanyATSSource):
    name = "smartrecruiters"

    def _fetch_company(self, client, company):
        slug = company["token"]
        base = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
        # `country=gb` filters server-side, so we only fetch the UK postings
        # (and only make a detail request for those).
        resp = request_with_retry(client, "GET", f"{base}?limit=100&country=gb")
        if resp.status_code != 200:
            return []
        out = []
        for raw in resp.json().get("content", []):
            # The list view has no description; each posting needs its own request.
            detail_resp = request_with_retry(client, "GET", f"{base}/{raw['id']}")
            detail = detail_resp.json() if detail_resp.status_code == 200 else {}
            sections = (detail.get("jobAd") or {}).get("sections") or {}
            description_html = "".join(
                f"<h3>{s.get('title', '')}</h3>{s.get('text', '')}"
                for s in sections.values()
                if isinstance(s, dict) and s.get("text")
            )
            loc = raw.get("location") or {}
            out.append(self._job(
                company,
                title=raw.get("name", ""),
                location=loc.get("fullLocation") or _join_location(loc.get("city"), loc.get("country")),
                employment_type=(raw.get("typeOfEmployment") or {}).get("label"),
                posted_at=_date_prefix(raw.get("releasedDate")),
                description_html=description_html,
                description_text=_html_to_text(description_html),
                apply_links=[ApplyLink(source=self.name, url=detail.get("postingUrl") or detail.get("applyUrl", ""))],
            ))
        return out


class PersonioSource(_CompanyATSSource):
    name = "personio"

    def _fetch_company(self, client, company):
        slug = company["token"]
        # Personio publishes XML (not JSON) at <company>.jobs.personio.de/xml.
        resp = request_with_retry(client, "GET", f"https://{slug}.jobs.personio.de/xml")
        if resp.status_code != 200:
            return []
        out = []
        for pos in ET.fromstring(resp.content).findall("position"):
            def text(tag: str) -> str:
                return (pos.findtext(tag) or "").strip()

            # Offices: the main one plus any additional ones, so a London
            # office listed second still passes the location filter.
            offices = [text("office")] + [
                (o.text or "").strip() for o in pos.findall("additionalOffices/office")
            ]
            description_html = "".join(
                f"<h3>{(d.findtext('name') or '').strip()}</h3>{d.findtext('value') or ''}"
                for d in pos.findall("jobDescriptions/jobDescription")
            )
            out.append(self._job(
                company,
                title=text("name"),
                location="; ".join(o for o in offices if o),
                employment_type=text("employmentType") or None,
                posted_at=_date_prefix(text("createdAt")),
                description_html=description_html,
                description_text=_html_to_text(description_html),
                apply_links=[ApplyLink(source=self.name, url=f"https://{slug}.jobs.personio.de/job/{text('id')}")],
            ))
        return out
