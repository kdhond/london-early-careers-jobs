"""
Refresh orchestration.

This is the piece that ties everything else together. A refresh runs this
pipeline (§6):

    fetch all sources in parallel
        -> normalise (figure out each job's city)
        -> extract (requirements, years required, salary, work mode)
        -> filter (0-4 years experience, England-only)
        -> dedupe (merge the same job seen on multiple sources)
        -> categorise (assign one of the categories in categories.yaml)
        -> upsert into SQLite
        -> mark jobs that disappeared as inactive

It can be triggered two ways:
  - From the web app: app.py calls run_refresh() in a background thread
    so POST /api/refresh returns immediately (see spec §8).
  - From the command line: `python -m jobboard.refresh`, so it can be
    scheduled with cron/launchd without the web server running at all.

Either way, progress is written to the `refresh_runs` table as it happens
(via db.update_refresh_run), so GET /api/refresh/status can report on an
in-progress refresh triggered from the web UI.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from jobboard import budget, db
from jobboard.categorise import Categoriser, categorise_jobs
from jobboard.dedupe import dedupe_jobs
from jobboard.extract import extract_requirements, extract_salary, extract_years_required, detect_work_mode
from jobboard.filters import is_early_career, is_in_england, normalise_city
from jobboard.models import Job
from jobboard.sources.adzuna import AdzunaSource
from jobboard.sources.ashby import AshbySource
from jobboard.sources.base import PROJECT_ROOT, load_yaml
from jobboard.sources.greenhouse import GreenhouseSource
from jobboard.sources.lever import LeverSource
from jobboard.sources.linkedin_apify import LinkedInApifySource
from jobboard.sources.reed import ReedSource

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "jobs.db"

# Free sources run inside a thread pool this wide — httpx releases the GIL
# during network waits, so this genuinely runs them concurrently, similar
# to how each source internally caps its own request concurrency at ~5.
SOURCE_THREAD_POOL_SIZE = 5


@dataclass
class RefreshSummary:
    """
    Everything the spec's final "done when" checklist (§13) asks for: jobs
    per source, duplicates removed, jobs dropped by each filter, and any
    problems with a source. Returned by run_refresh() and also what gets
    written into the refresh_runs table for the frontend's progress panel.
    """

    per_source: dict = field(default_factory=dict)  # {"reed": {"status": "done", "count": 42}, ...}
    dropped_by_experience_filter: int = 0
    dropped_by_location_filter: int = 0
    duplicates_removed: int = 0
    deactivated: int = 0
    final_job_count: int = 0

    def as_dict(self) -> dict:
        return {
            "sources": self.per_source,
            "dropped_by_experience_filter": self.dropped_by_experience_filter,
            "dropped_by_location_filter": self.dropped_by_location_filter,
            "duplicates_removed": self.duplicates_removed,
            "deactivated": self.deactivated,
            "final_job_count": self.final_job_count,
        }


def _fetch_free_source(name: str, source) -> tuple[str, list[Job], Optional[str]]:
    """Run one free source's safe_fetch() and report back (name, jobs, error-or-None)."""
    try:
        jobs = source.safe_fetch()
        return name, jobs, None
    except Exception as exc:  # safe_fetch() already catches most things, but be extra safe
        return name, [], str(exc)


def run_refresh(
    conn: sqlite3.Connection,
    settings: Optional[dict] = None,
    categoriser: Optional[Categoriser] = None,
    force_linkedin: bool = False,
    run_id: Optional[str] = None,
    on_progress=None,
) -> RefreshSummary:
    """
    Run one full refresh and return a RefreshSummary. `on_progress`, if
    given, is called with the current per-source status dict every time it
    changes — app.py uses this to update the refresh_runs table live, so
    GET /api/refresh/status can show progress while this is still running.
    """
    settings = settings or load_yaml("settings.yaml")
    categoriser = categoriser or Categoriser()
    run_id = run_id or str(uuid.uuid4())

    db.start_refresh_run(conn, run_id)
    summary = RefreshSummary()

    free_sources = {
        "greenhouse": GreenhouseSource(),
        "lever": LeverSource(),
        "ashby": AshbySource(),
        "reed": ReedSource(conn=conn),
        "adzuna": AdzunaSource(),
    }

    for name, source in free_sources.items():
        summary.per_source[name] = {
            "status": "pending" if source.is_configured() else "skipped",
            "count": 0,
        }
        if not source.is_configured():
            summary.per_source[name]["message"] = "not configured (no API key)"

    linkedin = LinkedInApifySource(conn=conn)
    is_first_linkedin_run = db.get_last_apify_run(conn) is None
    if not linkedin.is_configured():
        summary.per_source["linkedin"] = {
            "status": "skipped",
            "count": 0,
            "message": "not configured (no API key)",
        }
    else:
        budget_decision = budget.check_apify_budget(conn, settings, force=force_linkedin)
        if not budget_decision.allowed:
            summary.per_source["linkedin"] = {
                "status": "skipped",
                "count": 0,
                "message": budget_decision.reason,
            }
        else:
            summary.per_source["linkedin"] = {"status": "pending", "count": 0}

    def _report():
        if on_progress:
            on_progress(dict(summary.per_source))
        db.update_refresh_run(conn, run_id, summary.per_source)

    _report()

    all_jobs: list[Job] = []

    # Run every *configured, not-skipped* source concurrently. Each
    # source's own fetch() already caps its internal request concurrency
    # (see sources/base.py) — this pool is what makes the sources run
    # alongside each other, not just within each one.
    with concurrent.futures.ThreadPoolExecutor(max_workers=SOURCE_THREAD_POOL_SIZE) as pool:
        futures = {}
        for name, source in free_sources.items():
            if summary.per_source[name]["status"] != "pending":
                continue
            summary.per_source[name]["status"] = "running"
            futures[pool.submit(_fetch_free_source, name, source)] = name

        if summary.per_source.get("linkedin", {}).get("status") == "pending":
            summary.per_source["linkedin"]["status"] = "running"
            futures[
                pool.submit(lambda: ("linkedin", _safe_fetch_linkedin(linkedin, is_first_linkedin_run), None))
            ] = "linkedin"

        _report()

        for future in concurrent.futures.as_completed(futures):
            name, jobs, error = future.result()
            all_jobs.extend(jobs)
            if error:
                summary.per_source[name] = {"status": "error", "count": 0, "message": error}
            else:
                summary.per_source[name] = {"status": "done", "count": len(jobs)}
            _report()

    # --- normalise + extract -------------------------------------------------
    for job in all_jobs:
        job.city = normalise_city(job.location, settings)

        if not job.requirements:
            job.requirements = extract_requirements(job.description_html or job.description_text)

        if job.years_required is None:
            job.years_required = extract_years_required(job.description_text)

        if job.salary_min is None and job.salary_max is None:
            salary = extract_salary(job.description_text)
            if salary:
                job.salary_min = salary.min
                job.salary_max = salary.max
                job.salary_text = job.salary_text or salary.text

        if job.work_mode == "unknown":
            job.work_mode = detect_work_mode(f"{job.description_text} {job.location}")

    # --- filter ---------------------------------------------------------------
    after_experience_filter = [job for job in all_jobs if is_early_career(job, settings)]
    summary.dropped_by_experience_filter = len(all_jobs) - len(after_experience_filter)

    after_location_filter = [job for job in after_experience_filter if is_in_england(job, settings)]
    summary.dropped_by_location_filter = len(after_experience_filter) - len(after_location_filter)

    # --- dedupe -----------------------------------------------------------------
    deduped = dedupe_jobs(after_location_filter, settings)
    summary.duplicates_removed = len(after_location_filter) - len(deduped)

    # --- categorise --------------------------------------------------------------
    categorise_jobs(deduped, categoriser)

    # --- persist -----------------------------------------------------------------
    for job in deduped:
        db.upsert_job(conn, job)
        # A company's website (if we now know it) is worth caching against
        # its normalised name, so a *different* source's job at the same
        # company can use it later even if that source doesn't supply one.
        if job.company_website or job.company_logo:
            db.cache_company(conn, job.company_normalised, job.company_website, job.company_logo)
    conn.commit()

    summary.deactivated = db.mark_missing_inactive(
        conn, seen_ids=[job.id for job in deduped], inactive_after_missed=settings["inactive_after_missed_refreshes"]
    )
    summary.final_job_count = len(deduped)
    conn.commit()

    db.update_refresh_run(conn, run_id, summary.as_dict(), status="done")
    return summary


def _safe_fetch_linkedin(source: LinkedInApifySource, first_run: bool) -> list[Job]:
    try:
        return source.fetch(first_run=first_run)
    except Exception:
        import logging

        logging.getLogger("jobboard.refresh").exception("LinkedIn source failed")
        return []


def print_summary(summary: RefreshSummary) -> None:
    """Human-readable version of RefreshSummary, for the CLI entry point."""
    print("\nRefresh summary")
    print("=" * 40)
    for name, status in summary.per_source.items():
        line = f"  {name:12} {status['status']:8} {status['count']:4} jobs"
        if status.get("message"):
            line += f"  ({status['message']})"
        print(line)
    print()
    print(f"  Dropped by experience filter: {summary.dropped_by_experience_filter}")
    print(f"  Dropped by location filter:   {summary.dropped_by_location_filter}")
    print(f"  Duplicates merged away:       {summary.duplicates_removed}")
    print(f"  Jobs marked inactive:         {summary.deactivated}")
    print(f"  Active jobs after refresh:    {summary.final_job_count}")
    print("=" * 40)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a full jobboard refresh from the command line.")
    parser.add_argument(
        "--force-linkedin",
        action="store_true",
        help="Ignore the Apify cooldown (the monthly budget cap still applies).",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"Path to the SQLite database file (default: {DEFAULT_DB_PATH}).",
    )
    args = parser.parse_args()

    conn = db.get_db(args.db)
    summary = run_refresh(conn, force_linkedin=args.force_linkedin)
    print_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
