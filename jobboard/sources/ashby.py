"""
Ashby source.

Ashby is a newer ATS, popular with well-funded startups. Like Greenhouse and
Lever, it has a free public JSON API per company.

Docs: https://developers.ashbyhq.com/reference/jobpostingapi
"""
from __future__ import annotations

import re

import httpx

from jobboard.models import ApplyLink, Job
from jobboard.sources.base import USER_AGENT, Source, load_yaml, request_with_retry

_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(raw_html: str) -> str:
    if not raw_html:
        return ""
    return _TAG_RE.sub(" ", raw_html).strip()


class AshbySource(Source):
    name = "ashby"

    def __init__(self):
        companies = load_yaml("companies.yaml") or []
        self.companies = [c for c in companies if c.get("ats") == "ashby"]

    def is_configured(self) -> bool:
        return len(self.companies) > 0

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        headers = {"User-Agent": USER_AGENT}
        with httpx.Client(headers=headers) as client:
            for company in self.companies:
                slug = company["token"]
                url = (
                    f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
                    "?includeCompensation=true"
                )
                resp = request_with_retry(client, "GET", url)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                for raw in data.get("jobs", []):
                    jobs.append(self._to_job(raw, company))
        return jobs

    def _to_job(self, raw: dict, company: dict) -> Job:
        # Ashby's location field is usually a plain string like "London, UK",
        # but fall back to an "address" object if that's what's given instead.
        location = raw.get("location") or ""
        if not location:
            address = raw.get("address") or {}
            location = address.get("postalAddress", {}).get("addressLocality", "")

        description_html = raw.get("descriptionHtml", "") or ""
        description_text = _html_to_text(description_html) or raw.get("descriptionPlain", "")

        compensation = raw.get("compensation") or {}
        salary_text = compensation.get("compensationTierSummary")

        apply_url = raw.get("applyUrl") or raw.get("jobUrl", "")

        return Job.new(
            title=raw.get("title", ""),
            company=company["name"],
            location=location,
            source=self.name,
            company_website=company.get("website"),
            employment_type=raw.get("employmentType"),
            work_mode="remote" if raw.get("isRemote") else "unknown",
            posted_at=(raw.get("publishedAt") or "")[:10] or None,
            salary_text=salary_text,
            description_html=description_html,
            description_text=description_text,
            description_is_full=True,
            apply_links=[ApplyLink(source=self.name, url=apply_url)],
        )
