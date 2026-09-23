"""
LinkedIn source, via the Apify actor `supreme_coder/linkedin-jobs-scraper`.

This is the only source that costs real money and the only one that touches
LinkedIn — and only ever through Apify's servers, never by scraping
LinkedIn directly from this machine (that's how we avoid ever risking an IP
ban). See jobboard/budget.py for the cooldown + monthly spending cap that
decides *whether* refresh.py even calls this source.

IMPORTANT — mapping not yet confirmed against live data: the spec asks for
a small test run (count: 10) against the real actor before trusting the
field mapping below. The field names used in `_to_job()` are the ones
documented/expected for this actor, but they should be checked (and this
file corrected if needed) against a real response before relying on it.
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Optional
from urllib.parse import quote

import httpx

from jobboard.models import ApplyLink, Job
from jobboard.sources.base import USER_AGENT, Source, load_yaml

ACTOR = "supreme_coder~linkedin-jobs-scraper"
RUN_SYNC_URL = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"
RUN_ASYNC_URL = f"https://api.apify.com/v2/acts/{ACTOR}/runs"
RUN_STATUS_URL = "https://api.apify.com/v2/actor-runs/{run_id}"
DATASET_ITEMS_URL = "https://api.apify.com/v2/datasets/{dataset_id}/items"

SYNC_TIMEOUT_SECONDS = 120.0   # how long we wait for the sync endpoint before falling back
POLL_INTERVAL_SECONDS = 5.0
POLL_TIMEOUT_SECONDS = 300.0  # give up waiting on the async run after this long


class LinkedInApifySource(Source):
    name = "linkedin"

    def __init__(self, conn: Optional[sqlite3.Connection] = None):
        # `conn` is used to record the run's cost after it completes — see
        # jobboard/budget.py. Whether this source should run at all (the
        # cooldown/budget gate) is decided by the caller (refresh.py), not
        # here — this class just knows how to talk to Apify.
        self.conn = conn
        self.token = os.getenv("APIFY_TOKEN")
        settings = load_yaml("settings.yaml")
        self.apify_settings = settings["apify"]
        self.locations = settings["locations"]
        self.keywords: list[str] = settings["search_keywords"]
        self.skip_reason: Optional[str] = None  # set by refresh.py if this source is skipped

    def is_configured(self) -> bool:
        return bool(self.token)

    def fetch(self, first_run: bool = False, max_results: Optional[int] = None) -> list[Job]:
        """
        `first_run` selects whether we search "posted in the last week" or
        "posted in the last day" (spec §4.1). `max_results` overrides the
        configured cap — refresh.py may pass a smaller number for a routine
        refresh, since a first run needs to backfill more.
        """
        if not self.is_configured():
            return []

        max_results = max_results or self.apify_settings["max_results_per_run"]
        urls = self._build_search_urls(first_run)
        # Split the results budget evenly across search URLs. This is the
        # conservative choice: if the actor's `count` field turns out to
        # mean "per URL" rather than "in total", we still cannot exceed
        # max_results overall.
        count_per_url = max(1, max_results // len(urls))
        run_input = {"urls": urls, "count": count_per_url}

        items = self._run_sync_or_fallback(run_input)
        jobs = [self._to_job(raw) for raw in items]

        if self.conn is not None:
            from jobboard import budget  # local import to avoid a circular import at module load

            budget.record_run(self.conn, len(jobs), self.apify_settings["price_per_1000_usd"])

        return jobs

    def _build_search_urls(self, first_run: bool) -> list[str]:
        tpr = (
            self.apify_settings["posted_within_first_run"]
            if first_run
            else self.apify_settings["posted_within_routine"]
        )
        locations = [self.locations["primary"], *self.locations["secondary"]]
        urls = []
        for location in locations:
            for keyword in self.keywords:
                urls.append(
                    "https://www.linkedin.com/jobs/search/"
                    f"?keywords={quote(keyword)}"
                    f"&location={quote(location)}"
                    "&f_E=1%2C2%2C3"  # Internship, Entry level, Associate
                    f"&f_TPR={tpr}"
                )
        return urls

    def _run_sync_or_fallback(self, run_input: dict) -> list[dict]:
        """Try the fast synchronous endpoint first; fall back to async + polling if it times out."""
        url = f"{RUN_SYNC_URL}?token={self.token}"
        headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
        try:
            with httpx.Client(headers=headers, timeout=SYNC_TIMEOUT_SECONDS) as client:
                resp = client.post(url, json=run_input)
            if resp.status_code == 200:
                return resp.json()
        except httpx.TimeoutException:
            pass  # fall through to the async path below
        return self._run_async_and_poll(run_input)

    def _run_async_and_poll(self, run_input: dict) -> list[dict]:
        headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
        with httpx.Client(headers=headers, timeout=30.0) as client:
            start_resp = client.post(f"{RUN_ASYNC_URL}?token={self.token}", json=run_input)
            start_resp.raise_for_status()
            run = start_resp.json()["data"]
            run_id = run["id"]

            elapsed = 0.0
            status = run["status"]
            while status in ("READY", "RUNNING") and elapsed < POLL_TIMEOUT_SECONDS:
                time.sleep(POLL_INTERVAL_SECONDS)
                elapsed += POLL_INTERVAL_SECONDS
                status_resp = client.get(
                    f"{RUN_STATUS_URL.format(run_id=run_id)}?token={self.token}"
                )
                status_resp.raise_for_status()
                run = status_resp.json()["data"]
                status = run["status"]

            if status != "SUCCEEDED":
                return []  # timed out, failed or was aborted — refresh.py logs this as an error

            dataset_id = run["defaultDatasetId"]
            items_resp = client.get(
                f"{DATASET_ITEMS_URL.format(dataset_id=dataset_id)}?token={self.token}"
            )
            items_resp.raise_for_status()
            return items_resp.json()

    def _to_job(self, raw: dict) -> Job:
        # `.get(a) or raw.get(b)` chains below are fallbacks for field-name
        # variants — confirm the real names with a live test run and trim
        # these once known (see the module docstring).
        title = raw.get("title") or raw.get("jobTitle", "")
        company = raw.get("companyName") or raw.get("company", "")
        location = raw.get("location", "")
        company_website = raw.get("companyWebsite") or raw.get("companyLinkedinUrl")
        company_logo = raw.get("companyLogo")
        posted_at = raw.get("postedAt") or raw.get("postedDate")
        seniority = raw.get("seniorityLevel") or raw.get("seniority")
        description = raw.get("description", "") or ""
        apply_url = raw.get("applyUrl") or raw.get("jobUrl") or raw.get("link", "")

        return Job.new(
            title=title,
            company=company,
            location=location,
            source=self.name,
            company_website=company_website,
            company_logo=company_logo,
            posted_at=posted_at,
            seniority=seniority,
            employment_type=raw.get("employmentType"),
            description_html=description,
            description_text=description,
            description_is_full=True,
            apply_links=[ApplyLink(source=self.name, url=apply_url)],
        )
