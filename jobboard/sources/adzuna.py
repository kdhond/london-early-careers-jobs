"""
Adzuna source.

Adzuna aggregates job listings from many other boards. It has a free,
documented JSON API (needs a free `app_id` + `app_key`, both free):
https://developer.adzuna.com/

Adzuna's API has no "get the full description" endpoint — search results
are all we get, and the description is always a short snippet. So every
Adzuna job is stored with `description_is_full = False`, and the frontend
shows a "View full listing" link (to `redirect_url`) instead of the full
text.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

import httpx

from jobboard.models import ApplyLink, Job
from jobboard.sources.base import USER_AGENT, Source, load_yaml, request_with_retry

SEARCH_URL = "https://api.adzuna.com/v1/api/jobs/gb/search/{page}"


def _parse_adzuna_date(created: str) -> Optional[str]:
    """Adzuna gives ISO-ish timestamps like '2026-09-15T08:00:00Z'; keep just the date part."""
    if not created:
        return None
    try:
        return datetime.fromisoformat(created.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return created[:10] or None


class AdzunaSource(Source):
    name = "adzuna"

    def __init__(self):
        self.app_id = os.getenv("ADZUNA_APP_ID")
        self.app_key = os.getenv("ADZUNA_APP_KEY")
        settings = load_yaml("settings.yaml")
        self.max_pages_per_query: int = settings["adzuna"]["max_pages_per_query"]
        self.max_days_old: int = settings["adzuna"]["max_days_old"]

    def is_configured(self) -> bool:
        return bool(self.app_id and self.app_key)

    def fetch(self) -> list[Job]:
        if not self.is_configured():
            return []

        headers = {"User-Agent": USER_AGENT}
        jobs_by_id: dict[str, Job] = {}
        with httpx.Client(headers=headers) as client:
            # No `what` (keyword) param: search by location only. Live-
            # checked against Adzuna's real API: London alone returns
            # ~110,000 total results vs. ~640 for what="graduate" — keyword
            # search was silently missing almost everything. Two passes
            # ("London", then no `where` at all) so nationwide/remote
            # postings are caught too, same reasoning as reed.py — the
            # location filter downstream (filters.py) drops anything not
            # actually in England or UK-remote.
            for where in ("London", None):
                for page in range(1, self.max_pages_per_query + 1):
                    raws = self._search(client, where, page)
                    if not raws:
                        break  # ran out of pages for this location
                    for raw in raws:
                        job = self._to_job(raw)
                        jobs_by_id[job.id] = job  # dedupe across location/page overlap
        return list(jobs_by_id.values())

    def _search(self, client: httpx.Client, where: Optional[str], page: int) -> list[dict]:
        url = SEARCH_URL.format(page=page)
        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "results_per_page": 50,
            "max_days_old": self.max_days_old,
            "content-type": "application/json",
        }
        if where:
            params["where"] = where
        resp = request_with_retry(client, "GET", url, params=params)
        if resp.status_code != 200:
            return []
        return resp.json().get("results", [])

    def _to_job(self, raw: dict) -> Job:
        company = (raw.get("company") or {}).get("display_name", "")
        location = (raw.get("location") or {}).get("display_name", "")

        return Job.new(
            title=raw.get("title", ""),
            company=company,
            location=location,
            source=self.name,
            posted_at=_parse_adzuna_date(raw.get("created", "")),
            salary_min=raw.get("salary_min"),
            salary_max=raw.get("salary_max"),
            employment_type=raw.get("contract_type") or raw.get("contract_time"),
            description_html=raw.get("description", "") or "",
            description_text=raw.get("description", "") or "",
            description_is_full=False,  # Adzuna never gives the full posting
            apply_links=[ApplyLink(source=self.name, url=raw.get("redirect_url", ""))],
        )
