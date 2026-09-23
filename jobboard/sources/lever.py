"""
Lever source.

Lever is another ATS, like Greenhouse. It also publishes a free public JSON
API per company. Some companies run on Lever's EU-hosted instance instead of
the default one, so we try the default URL first and fall back to the EU
one if that returns nothing.

Docs: https://github.com/lever/postings-api
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx

from jobboard.models import ApplyLink, Job
from jobboard.sources.base import USER_AGENT, Source, load_yaml, request_with_retry

_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(raw_html: str) -> str:
    if not raw_html:
        return ""
    return _TAG_RE.sub(" ", raw_html).strip()


class LeverSource(Source):
    name = "lever"

    def __init__(self):
        companies = load_yaml("companies.yaml") or []
        self.companies = [c for c in companies if c.get("ats") == "lever"]

    def is_configured(self) -> bool:
        return len(self.companies) > 0

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        headers = {"User-Agent": USER_AGENT}
        with httpx.Client(headers=headers) as client:
            for company in self.companies:
                slug = company["token"]
                # Try the standard instance first...
                data = self._fetch_postings(client, "https://api.lever.co/v0/postings", slug)
                if not data:
                    # ...and fall back to the EU instance if that came back empty.
                    data = self._fetch_postings(
                        client, "https://api.eu.lever.co/v0/postings", slug
                    )
                for raw in data:
                    jobs.append(self._to_job(raw, company))
        return jobs

    def _fetch_postings(self, client: httpx.Client, base_url: str, slug: str) -> list[dict]:
        url = f"{base_url}/{slug}?mode=json"
        resp = request_with_retry(client, "GET", url)
        if resp.status_code != 200:
            return []
        try:
            data = resp.json()
        except ValueError:
            return []
        return data if isinstance(data, list) else []

    def _to_job(self, raw: dict, company: dict) -> Job:
        categories = raw.get("categories", {}) or {}
        location = categories.get("location", "") or ""

        description_html = raw.get("description", "") or ""
        # Lever also breaks the posting into named sections (e.g.
        # "Requirements", "What you'll do") under `lists`. We append these
        # to the description so extract.py's heading-based requirement
        # parser can find them too.
        for section in raw.get("lists", []) or []:
            heading = section.get("text", "")
            content = section.get("content", "")
            if heading or content:
                description_html += f"<h3>{heading}</h3>{content}"

        # Derived from the SAME description_html built above (base
        # description + appended "lists" sections), not Lever's own
        # descriptionPlain field — that field only covers the base
        # description and silently drops the appended sections, which is
        # often where the actual requirements/responsibilities live.
        description_text = _html_to_text(description_html)

        # Lever gives createdAt as milliseconds since the Unix epoch.
        posted_at = None
        created_at_ms = raw.get("createdAt")
        if created_at_ms:
            posted_at = datetime.fromtimestamp(
                created_at_ms / 1000, tz=timezone.utc
            ).date().isoformat()

        apply_url = raw.get("applyUrl") or raw.get("hostedUrl", "")

        return Job.new(
            title=raw.get("text", ""),
            company=company["name"],
            location=location,
            source=self.name,
            company_website=company.get("website"),
            employment_type=categories.get("commitment"),
            posted_at=posted_at,
            description_html=description_html,
            description_text=description_text,
            description_is_full=True,
            apply_links=[ApplyLink(source=self.name, url=apply_url)],
        )
