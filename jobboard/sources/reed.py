"""
Reed source.

Reed is a big UK job board with a free, documented JSON API (you need to
sign up for a key, but it costs nothing): https://www.reed.co.uk/developers/jobseeker

Two-step process, because Reed's search results only include a short
snippet of each job's description:
  1. Search for jobs by keyword/location -> get a snippet (~450 chars) per job.
  2. For any job we haven't already stored with a full description, fetch
     the full description from the job-details endpoint. We only do this
     for *new* jobs (checked against the database) so a routine refresh
     doesn't re-fetch details for jobs we already have.
"""
from __future__ import annotations

import concurrent.futures
import os
import sqlite3
import time
from datetime import datetime
from typing import Optional

import httpx

from jobboard.models import ApplyLink, Job
from jobboard.sources.base import USER_AGENT, Source, load_yaml, request_with_retry

SEARCH_URL = "https://www.reed.co.uk/api/1.0/search"
JOB_URL = "https://www.reed.co.uk/api/1.0/jobs/{job_id}"


def _parse_reed_date(date_str: str) -> Optional[str]:
    """Reed gives dates as DD/MM/YYYY; we store dates as ISO (YYYY-MM-DD)."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


class ReedSource(Source):
    name = "reed"

    def __init__(self, conn: Optional[sqlite3.Connection] = None):
        # `conn` lets us check "have we already fetched this job's full
        # description before?" so repeat refreshes don't re-fetch every job.
        self.conn = conn
        self.api_key = os.getenv("REED_API_KEY")
        settings = load_yaml("settings.yaml")
        self.results_per_query: int = settings["reed"]["results_per_query"]
        self.detail_concurrency: int = settings["reed"]["detail_concurrency"]

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def fetch(self) -> list[Job]:
        if not self.is_configured():
            return []

        # Reed's API uses HTTP Basic auth with the API key as the username
        # and an empty password — there's no real password involved.
        auth = (self.api_key, "")
        headers = {"User-Agent": USER_AGENT}

        raw_jobs_by_id: dict[str, dict] = {}
        with httpx.Client(auth=auth, headers=headers) as client:
            # No `keywords` param at all: search by location only, so we
            # aren't limited to jobs whose title happens to contain one of
            # our configured search words. Live-checked against Reed's real
            # API: London alone returns ~27,000 total results vs. ~1,300
            # for keyword="graduate" — keyword search was silently missing
            # the vast majority of jobs. Two passes (London, then no
            # location at all) so England-wide/remote postings are caught
            # too — the location filter downstream (filters.py) drops
            # anything that isn't actually in England or UK-remote.
            for location_name in ("London", None):
                for raw in self._search(client, location_name):
                    raw_jobs_by_id[str(raw["jobId"])] = raw

            self._fill_full_descriptions(client, raw_jobs_by_id)

        return [self._to_job(raw) for raw in raw_jobs_by_id.values()]

    def _search(self, client: httpx.Client, location_name: Optional[str]) -> list[dict]:
        """Page through Reed's search results for one location (no keyword filter)."""
        results: list[dict] = []
        skip = 0
        page_size = 100  # Reed's max page size
        while len(results) < self.results_per_query:
            params = {
                "distanceFromLocation": 25,
                "resultsToTake": page_size,
                "resultsToSkip": skip,
            }
            if location_name:
                params["locationName"] = location_name
            resp = request_with_retry(client, "GET", SEARCH_URL, params=params)
            if resp.status_code != 200:
                break
            data = resp.json()
            page = data.get("results", [])
            if not page:
                break  # no more results
            results.extend(page)
            skip += page_size
            if len(page) < page_size:
                break  # reached the last page
        return results[: self.results_per_query]

    def _fill_full_descriptions(
        self, client: httpx.Client, raw_jobs_by_id: dict[str, dict]
    ) -> None:
        """
        For every job we don't already have a full description for, fetch
        one from Reed's job-details endpoint. Runs a small batch of
        requests at a time (detail_concurrency) with a short pause between
        batches, to stay well within Reed's rate limits.
        """
        to_fetch = [
            job_id
            for job_id, raw in raw_jobs_by_id.items()
            if not self._already_have_full_description(raw)
        ]

        for i in range(0, len(to_fetch), self.detail_concurrency):
            batch = to_fetch[i : i + self.detail_concurrency]
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=self.detail_concurrency
            ) as pool:
                futures = {
                    job_id: pool.submit(self._fetch_detail, client, job_id) for job_id in batch
                }
                for job_id, future in futures.items():
                    detail = future.result()
                    if detail:
                        raw_jobs_by_id[job_id].update(detail)
            time.sleep(0.5)  # be polite between batches

    def _already_have_full_description(self, raw: dict) -> bool:
        """Check the database for a job matching this Reed listing that already has a full description."""
        if self.conn is None:
            return False
        from jobboard.db import get_job  # local import to avoid a circular import at module load

        candidate = self._to_job(raw)
        existing = get_job(self.conn, candidate.id)
        return existing is not None and existing.description_is_full

    def _fetch_detail(self, client: httpx.Client, job_id: str) -> Optional[dict]:
        resp = request_with_retry(client, "GET", JOB_URL.format(job_id=job_id))
        if resp.status_code != 200:
            return None
        return resp.json()

    def _to_job(self, raw: dict) -> Job:
        description = raw.get("jobDescription", "") or ""

        apply_links = [ApplyLink(source=self.name, url=raw.get("jobUrl", ""))]
        external_url = raw.get("externalUrl")
        if external_url:
            apply_links.append(ApplyLink(source="employer", url=external_url))

        salary_min = raw.get("minimumSalary")
        salary_max = raw.get("maximumSalary")
        salary_text = None
        if salary_min or salary_max:
            currency = raw.get("currency", "GBP")
            salary_text = f"{currency} {salary_min or ''}-{salary_max or ''}".strip()

        return Job.new(
            title=raw.get("jobTitle", ""),
            company=raw.get("employerName", ""),
            location=raw.get("locationName", ""),
            source=self.name,
            posted_at=_parse_reed_date(raw.get("date", "")),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_text=salary_text,
            employment_type=raw.get("contractType"),
            description_html=description,
            description_text=description,
            # We only know we have the *full* description once we've hit the
            # job-details endpoint successfully (it adds "externalUrl").
            description_is_full="externalUrl" in raw,
            apply_links=apply_links,
        )
