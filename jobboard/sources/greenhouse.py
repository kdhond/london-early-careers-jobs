"""
Greenhouse source.

Greenhouse is an ATS (Applicant Tracking System) that many companies use to
run their careers page. It publishes a free, public, no-key-required JSON
API for each company's job board, which is exactly what we want: real
company data, always up to date, no risk of getting blocked.

Docs: https://developers.greenhouse.io/job-board.html
"""
from __future__ import annotations

import html
import re

import httpx

from jobboard.models import ApplyLink, Job
from jobboard.sources.base import (
    USER_AGENT,
    Source,
    load_yaml,
    request_with_retry,
)

# A crude but effective HTML-tag stripper for turning Greenhouse's HTML
# description into plain text (used for search/matching, not for display —
# the sanitised HTML is what actually gets shown on the page).
_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(raw_html: str) -> str:
    """Unescape HTML entities (e.g. &amp;) then strip all tags, leaving plain text."""
    if not raw_html:
        return ""
    unescaped = html.unescape(raw_html)
    return _TAG_RE.sub(" ", unescaped).strip()


class GreenhouseSource(Source):
    name = "greenhouse"

    def __init__(self):
        # Only load companies whose `ats` field is "greenhouse" — the same
        # companies.yaml file is shared between greenhouse.py, lever.py and
        # ashby.py, each one filtering to its own rows.
        companies = load_yaml("companies.yaml") or []
        self.companies = [c for c in companies if c.get("ats") == "greenhouse"]

    def is_configured(self) -> bool:
        # No API key needed — this source works as long as we have at least
        # one company configured for it.
        return len(self.companies) > 0

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        headers = {"User-Agent": USER_AGENT}
        with httpx.Client(headers=headers) as client:
            for company in self.companies:
                token = company["token"]
                url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
                resp = request_with_retry(client, "GET", url)
                if resp.status_code != 200:
                    # This one company's board might be gone or renamed;
                    # skip it and keep going with the rest.
                    continue
                data = resp.json()
                for raw in data.get("jobs", []):
                    jobs.append(self._to_job(raw, company))
        return jobs

    def _to_job(self, raw: dict, company: dict) -> Job:
        location = (raw.get("location") or {}).get("name", "")
        description_html = raw.get("content", "") or ""
        description_text = _html_to_text(description_html)

        return Job.new(
            title=raw.get("title", ""),
            company=company["name"],
            location=location,
            source=self.name,
            company_website=company.get("website"),
            category_hint=company.get("category_hint"),
            posted_at=(raw.get("updated_at") or "")[:10] or None,  # YYYY-MM-DD prefix
            description_html=description_html,
            description_text=description_text,
            description_is_full=True,  # Greenhouse always gives the full posting
            apply_links=[ApplyLink(source=self.name, url=raw.get("absolute_url", ""))],
        )
