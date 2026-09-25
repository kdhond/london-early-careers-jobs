"""
LinkedIn source, via the Apify actor `valig/linkedin-jobs-scraper`.

This is the only source that costs real money and the only one that touches
LinkedIn — and only ever through Apify's servers, never by scraping
LinkedIn directly from this machine (that's how we avoid ever risking an IP
ban). See jobboard/budget.py for the cooldown + monthly spending cap that
decides *whether* refresh.py even calls this source.

A note on how this actor was chosen: the spec originally named a different
actor, `supreme_coder/linkedin-jobs-scraper`, but calling it now returns
"Actor with this name was not found" — it's been removed from Apify. This
file uses `valig/linkedin-jobs-scraper` instead, picked after comparing it
against the alternatives on Apify's store: it's ~5x cheaper than the next
best option, at the cost of not being able to scrape a company's real
website directly (we already have a fallback for that — see §5.1 and
jobboard/db.py's `companies` cache). All the field names below were
confirmed with a real (paid, ~$0.001) test run against the live actor
before writing this mapping.

Unlike the actor the spec described, this one takes one keyword and one
location per call (not a list of full LinkedIn search URLs) — so a full
LinkedIn search means one actor run per keyword in linkedin_search_keywords, all
searching the single location "England, United Kingdom" (which already
covers London). That's a deliberate cost-saving choice: this actor charges
a small flat fee every time it starts, on top of its per-result price, so
searching one broad location instead of two roughly halves that overhead.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Optional

import httpx

from jobboard.models import ApplyLink, Job
from jobboard.sources.base import USER_AGENT, Source, load_yaml

ACTOR = "valig~linkedin-jobs-scraper"
RUN_SYNC_URL = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"
RUN_ASYNC_URL = f"https://api.apify.com/v2/acts/{ACTOR}/runs"
RUN_STATUS_URL = "https://api.apify.com/v2/actor-runs/{run_id}"
DATASET_ITEMS_URL = "https://api.apify.com/v2/datasets/{dataset_id}/items"

# LinkedIn's own search filter codes, passed straight through via the
# actor's `urlParam` field: f_E is "experience level", 1/2/3 = Internship,
# Entry level, Associate — the same filter the spec's original design used.
EXPERIENCE_LEVEL_FILTER = {"key": "f_E", "value": "1,2,3"}

SYNC_TIMEOUT_SECONDS = 90.0   # how long we wait for one keyword's sync call before falling back
POLL_INTERVAL_SECONDS = 5.0
POLL_TIMEOUT_SECONDS = 180.0  # give up waiting on the async run after this long


class LinkedInApifySource(Source):
    name = "linkedin"

    def __init__(self, conn: Optional[sqlite3.Connection] = None):
        # `conn` is used to record the run's cost after it completes — see
        # jobboard/budget.py. Whether this source should run at all (the
        # cooldown/budget gate) is decided by the caller (refresh.py), not
        # here — this class just knows how to talk to Apify.
        self.conn = conn
        settings = load_yaml("settings.yaml")
        self.token = self._read_token()
        self.apify_settings = settings["apify"]
        self.keywords: list[str] = settings["linkedin_search_keywords"]
        self.skip_reason: Optional[str] = None  # set by refresh.py if this source is skipped

    @staticmethod
    def _read_token() -> Optional[str]:
        import os

        return os.getenv("APIFY_TOKEN")

    def is_configured(self) -> bool:
        return bool(self.token)

    def fetch(self, first_run: bool = False, max_results: Optional[int] = None) -> list[Job]:
        """
        `first_run` selects whether we search "posted in the last week" or
        "posted in the last day" (spec §4.1). `max_results` overrides the
        configured cap — refresh.py may pass a smaller number for a
        routine refresh than for a first run.
        """
        if not self.is_configured():
            return []

        max_results = max_results or self.apify_settings["max_results_per_run"]
        # Split the results budget evenly across the one run we make per
        # keyword.
        limit_per_keyword = max(1, max_results // len(self.keywords))
        date_posted = (
            self.apify_settings["posted_within_first_run"]
            if first_run
            else self.apify_settings["posted_within_routine"]
        )

        raw_jobs: list[dict] = []
        headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
        with httpx.Client(headers=headers) as client:
            for keyword in self.keywords:
                run_input = {
                    "keywords": keyword,
                    "location": "England, United Kingdom",
                    "datePosted": date_posted,
                    "limit": limit_per_keyword,
                    "urlParam": [EXPERIENCE_LEVEL_FILTER],
                }
                raw_jobs.extend(self._run_sync_or_fallback(client, run_input))

        jobs_by_id: dict[str, Job] = {}
        for raw in raw_jobs:
            job = self._to_job(raw)
            jobs_by_id[job.id] = job  # dedupe overlap between keyword searches

        if self.conn is not None:
            from jobboard import budget  # local import to avoid a circular import at module load

            budget.record_run(
                self.conn,
                len(jobs_by_id),
                self.apify_settings["price_per_1000_usd"],
                num_searches=len(self.keywords),
                actor_start_fee_usd=self.apify_settings.get("actor_start_fee_usd", 0.0),
            )

        return list(jobs_by_id.values())

    def _run_sync_or_fallback(self, client: httpx.Client, run_input: dict) -> list[dict]:
        """Try the fast synchronous endpoint first; fall back to async + polling if it times out."""
        url = f"{RUN_SYNC_URL}?token={self.token}"
        try:
            resp = client.post(url, json=run_input, timeout=SYNC_TIMEOUT_SECONDS)
            if resp.status_code in (200, 201):
                return resp.json()
        except httpx.TimeoutException:
            pass  # fall through to the async path below
        return self._run_async_and_poll(client, run_input)

    def _run_async_and_poll(self, client: httpx.Client, run_input: dict) -> list[dict]:
        start_resp = client.post(
            f"{RUN_ASYNC_URL}?token={self.token}", json=run_input, timeout=30.0
        )
        start_resp.raise_for_status()
        run = start_resp.json()["data"]
        run_id = run["id"]

        elapsed = 0.0
        status = run["status"]
        while status in ("READY", "RUNNING") and elapsed < POLL_TIMEOUT_SECONDS:
            time.sleep(POLL_INTERVAL_SECONDS)
            elapsed += POLL_INTERVAL_SECONDS
            status_resp = client.get(
                f"{RUN_STATUS_URL.format(run_id=run_id)}?token={self.token}", timeout=15.0
            )
            status_resp.raise_for_status()
            run = status_resp.json()["data"]
            status = run["status"]

        if status != "SUCCEEDED":
            return []  # timed out, failed or was aborted — refresh.py logs this as an error

        dataset_id = run["defaultDatasetId"]
        items_resp = client.get(
            f"{DATASET_ITEMS_URL.format(dataset_id=dataset_id)}?token={self.token}", timeout=30.0
        )
        items_resp.raise_for_status()
        return items_resp.json()

    def _to_job(self, raw: dict) -> Job:
        # We deliberately do NOT map `companyUrl` to company_website: it's
        # a link to the company's *LinkedIn page*, not their real website,
        # and the spec says "never make up a domain" — so we leave
        # company_website unset here and let the normal fallback chain
        # (companies.yaml -> cached website -> "Find website" link, §5.1)
        # fill it in instead.
        apply_url = raw.get("applyUrl") or raw.get("url", "")

        return Job.new(
            title=raw.get("title", ""),
            company=raw.get("companyName", ""),
            location=raw.get("location", ""),
            source=self.name,
            posted_at=(raw.get("postedDate") or "")[:10] or None,
            seniority=raw.get("experienceLevel"),
            employment_type=raw.get("contractType"),
            salary_text=raw.get("salary") or None,
            description_html=raw.get("descriptionHtml", "") or "",
            description_text=raw.get("description", "") or "",
            description_is_full=True,
            apply_links=[ApplyLink(source=self.name, url=apply_url)],
        )
