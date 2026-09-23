"""
SQLite storage layer.

This is the only file that talks to the database directly — everything else
(sources, the pipeline, the Flask routes) goes through the functions here.
Keeping all the SQL in one place makes it much easier to change the schema
later without hunting through the rest of the codebase.

A few design choices worth knowing:
- We store list/dict fields (requirements, apply_links, sources) as JSON
  text in a column, because SQLite doesn't have a native list type and this
  app doesn't need to query *inside* those lists with SQL.
- `upsert_job` uses SQLite's "INSERT ... ON CONFLICT DO UPDATE" so that if a
  job with the same id already exists, we update it in place instead of
  creating a duplicate row.
- `missed_refreshes` tracks how many refreshes in a row a job has NOT been
  seen in. Once a job is missing for `inactive_after_missed` refreshes in a
  row, we mark it inactive (see §6, "mark jobs that have disappeared as
  inactive" in the spec) rather than deleting it, so history is kept.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from jobboard.models import ApplyLink, Job

# The SQL that creates every table, run once whenever we open the database.
# "CREATE TABLE IF NOT EXISTS" makes this safe to run every time the app
# starts, even if the tables already exist from a previous run.
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    company_normalised TEXT NOT NULL,
    company_website TEXT,
    company_logo TEXT,
    location TEXT,
    city TEXT,
    work_mode TEXT DEFAULT 'unknown',
    posted_at TEXT,
    posted_at_estimated INTEGER DEFAULT 0,
    first_seen_at TEXT,
    last_seen_at TEXT,
    salary_min REAL,
    salary_max REAL,
    salary_text TEXT,
    employment_type TEXT,
    seniority TEXT,
    years_required INTEGER,
    category TEXT DEFAULT 'Other',
    description_html TEXT,
    description_text TEXT,
    description_is_full INTEGER DEFAULT 0,
    requirements TEXT DEFAULT '[]',
    apply_links TEXT DEFAULT '[]',
    sources TEXT DEFAULT '[]',
    is_active INTEGER DEFAULT 1,
    missed_refreshes INTEGER DEFAULT 0
);

-- Indexes speed up the queries the app actually runs: "give me active jobs",
-- and dedupe's "find other jobs at the same company".
CREATE INDEX IF NOT EXISTS idx_jobs_active ON jobs(is_active);
CREATE INDEX IF NOT EXISTS idx_jobs_company_norm ON jobs(company_normalised);
CREATE INDEX IF NOT EXISTS idx_jobs_posted_at ON jobs(posted_at);

-- Caches a company's website/logo once we learn it from any source, so a
-- later job at the same company (from a source with no website info) can
-- still show a website link. See spec §5.1.
CREATE TABLE IF NOT EXISTS companies (
    company_normalised TEXT PRIMARY KEY,
    website TEXT,
    logo TEXT
);

-- One row per refresh (each time the Refresh button is pressed, or the
-- scheduled `python -m jobboard.refresh` runs). Lets the frontend poll
-- "how is the current refresh going" via GET /api/refresh/status.
CREATE TABLE IF NOT EXISTS refresh_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT DEFAULT 'running',
    sources_json TEXT DEFAULT '{}'
);

-- One row per Apify (LinkedIn) run, used to enforce the monthly budget cap
-- in jobboard/budget.py — we sum estimated_cost_usd for the current month.
CREATE TABLE IF NOT EXISTS apify_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at TEXT NOT NULL,
    results INTEGER NOT NULL,
    estimated_cost_usd REAL NOT NULL
);
"""


def get_db(path: str | Path) -> sqlite3.Connection:
    """Open (or create) the SQLite database file and make sure the schema exists."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)  # make sure data/ exists
    # check_same_thread=False because Flask's refresh runs in a background
    # thread (see app.py) while the main thread keeps serving requests.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row  # lets us access columns by name, e.g. row["title"]
    conn.execute("PRAGMA journal_mode=WAL")  # allows concurrent reads while a write is happening
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Commit on success, roll back if anything inside the `with` block raises."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _now() -> str:
    """Current UTC time as an ISO 8601 string, used for timestamp columns."""
    return datetime.now(timezone.utc).isoformat()


def _row_to_job(row: sqlite3.Row) -> Job:
    """Convert one database row back into a Job object (the reverse of upsert_job)."""
    d = dict(row)
    # The JSON text columns need to be parsed back into Python lists/objects.
    apply_links = [ApplyLink(**l) for l in json.loads(d.pop("apply_links") or "[]")]
    requirements = json.loads(d.pop("requirements") or "[]")
    sources = json.loads(d.pop("sources") or "[]")
    d.pop("missed_refreshes", None)  # internal bookkeeping field, not part of the Job schema
    # SQLite stores booleans as 0/1 integers, so convert them back to True/False.
    d["description_is_full"] = bool(d["description_is_full"])
    d["posted_at_estimated"] = bool(d["posted_at_estimated"])
    d["is_active"] = bool(d["is_active"])
    return Job(apply_links=apply_links, requirements=requirements, sources=sources, **d)


def upsert_job(conn: sqlite3.Connection, job: Job) -> None:
    """
    Insert a new job, or update it in place if a job with the same id
    already exists (that's what "upsert" means: UPDATE or INSERT).

    Because `job.id` is a hash of company + title + location (see models.py),
    the same real-world job seen again in a later refresh will produce the
    same id and land on the same row here, instead of creating a duplicate.
    """
    now = _now()
    # Keep the original first_seen_at if this job already existed, so we
    # don't lose track of when we first spotted it.
    existing = conn.execute(
        "SELECT first_seen_at FROM jobs WHERE id = ?", (job.id,)
    ).fetchone()
    first_seen_at = existing["first_seen_at"] if existing else now
    job.first_seen_at = first_seen_at
    job.last_seen_at = now

    conn.execute(
        """
        INSERT INTO jobs (
            id, title, company, company_normalised, company_website, company_logo,
            location, city, work_mode, posted_at, posted_at_estimated,
            first_seen_at, last_seen_at, salary_min, salary_max, salary_text,
            employment_type, seniority, years_required, category,
            description_html, description_text, description_is_full,
            requirements, apply_links, sources, is_active, missed_refreshes
        ) VALUES (
            :id, :title, :company, :company_normalised, :company_website, :company_logo,
            :location, :city, :work_mode, :posted_at, :posted_at_estimated,
            :first_seen_at, :last_seen_at, :salary_min, :salary_max, :salary_text,
            :employment_type, :seniority, :years_required, :category,
            :description_html, :description_text, :description_is_full,
            :requirements, :apply_links, :sources, 1, 0
        )
        -- If a row with this id already exists, update it instead of failing
        -- with a "duplicate primary key" error. We also reset is_active to 1
        -- and missed_refreshes to 0, since seeing the job again means it's
        -- still live.
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title, company=excluded.company,
            company_normalised=excluded.company_normalised,
            company_website=excluded.company_website, company_logo=excluded.company_logo,
            location=excluded.location, city=excluded.city, work_mode=excluded.work_mode,
            posted_at=excluded.posted_at, posted_at_estimated=excluded.posted_at_estimated,
            last_seen_at=excluded.last_seen_at,
            salary_min=excluded.salary_min, salary_max=excluded.salary_max,
            salary_text=excluded.salary_text, employment_type=excluded.employment_type,
            seniority=excluded.seniority, years_required=excluded.years_required,
            category=excluded.category, description_html=excluded.description_html,
            description_text=excluded.description_text,
            description_is_full=excluded.description_is_full,
            requirements=excluded.requirements, apply_links=excluded.apply_links,
            sources=excluded.sources, is_active=1, missed_refreshes=0
        """,
        {
            "id": job.id,
            "title": job.title,
            "company": job.company,
            "company_normalised": job.company_normalised,
            "company_website": job.company_website,
            "company_logo": job.company_logo,
            "location": job.location,
            "city": job.city,
            "work_mode": job.work_mode,
            "posted_at": job.posted_at,
            "posted_at_estimated": int(job.posted_at_estimated),
            "first_seen_at": job.first_seen_at,
            "last_seen_at": job.last_seen_at,
            "salary_min": job.salary_min,
            "salary_max": job.salary_max,
            "salary_text": job.salary_text,
            "employment_type": job.employment_type,
            "seniority": job.seniority,
            "years_required": job.years_required,
            "category": job.category,
            "description_html": job.description_html,
            "description_text": job.description_text,
            "description_is_full": int(job.description_is_full),
            "requirements": json.dumps(job.requirements),
            "apply_links": json.dumps([l.to_dict() for l in job.apply_links]),
            "sources": json.dumps(job.sources),
        },
    )


def mark_missing_inactive(
    conn: sqlite3.Connection, seen_ids: Iterable[str], inactive_after_missed: int
) -> int:
    """
    Called once per refresh, after all sources have been upserted.

    Any active job whose id was NOT in this refresh's results gets its
    `missed_refreshes` counter bumped. Once that counter reaches
    `inactive_after_missed`, the job is marked inactive (so it stops
    appearing on the board) — this handles jobs that have been taken down
    without us needing to delete the row.

    Returns how many jobs were newly deactivated, for the refresh summary.
    """
    seen_ids = set(seen_ids)
    rows = conn.execute("SELECT id, missed_refreshes FROM jobs WHERE is_active = 1").fetchall()
    deactivated = 0
    for row in rows:
        if row["id"] in seen_ids:
            continue  # still present this refresh, nothing to do
        missed = row["missed_refreshes"] + 1
        if missed >= inactive_after_missed:
            conn.execute(
                "UPDATE jobs SET is_active = 0, missed_refreshes = ? WHERE id = ?",
                (missed, row["id"]),
            )
            deactivated += 1
        else:
            conn.execute(
                "UPDATE jobs SET missed_refreshes = ? WHERE id = ?", (missed, row["id"])
            )
    return deactivated


def get_active_jobs(conn: sqlite3.Connection) -> list[Job]:
    """All jobs currently shown on the board (used by GET /api/jobs)."""
    rows = conn.execute("SELECT * FROM jobs WHERE is_active = 1").fetchall()
    return [_row_to_job(r) for r in rows]


def get_job(conn: sqlite3.Connection, job_id: str) -> Optional[Job]:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def get_company_website(conn: sqlite3.Connection, company_normalised: str) -> Optional[dict]:
    """Look up a cached website/logo for a company we've seen before (spec §5.1, priority 3)."""
    row = conn.execute(
        "SELECT website, logo FROM companies WHERE company_normalised = ?",
        (company_normalised,),
    ).fetchone()
    return dict(row) if row else None


def cache_company(
    conn: sqlite3.Connection,
    company_normalised: str,
    website: Optional[str] = None,
    logo: Optional[str] = None,
) -> None:
    """
    Remember a company's website/logo so future jobs at the same company
    (even from a source that doesn't provide this info) can reuse it.
    COALESCE means "keep the existing value if the new one is empty" —
    so a later call with only a logo won't wipe out an already-known website.
    """
    if not company_normalised or not (website or logo):
        return
    conn.execute(
        """
        INSERT INTO companies (company_normalised, website, logo) VALUES (?, ?, ?)
        ON CONFLICT(company_normalised) DO UPDATE SET
            website = COALESCE(excluded.website, companies.website),
            logo = COALESCE(excluded.logo, companies.logo)
        """,
        (company_normalised, website, logo),
    )


def start_refresh_run(conn: sqlite3.Connection, run_id: str) -> None:
    """Record that a refresh has started, so /api/refresh/status has something to report."""
    conn.execute(
        "INSERT INTO refresh_runs (run_id, started_at, status) VALUES (?, ?, 'running')",
        (run_id, _now()),
    )
    conn.commit()


def update_refresh_run(
    conn: sqlite3.Connection, run_id: str, sources_status: dict, status: Optional[str] = None
) -> None:
    """
    Update the progress of an in-flight refresh. `sources_status` is a dict
    like {"reed": {"status": "done", "count": 42}, ...} that the frontend's
    progress panel polls and displays. Pass `status="done"` (or "error")
    when the whole refresh finishes.
    """
    fields = {"sources_json": json.dumps(sources_status)}
    if status:
        fields["status"] = status
        fields["finished_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE refresh_runs SET {set_clause} WHERE run_id = ?",
        (*fields.values(), run_id),
    )
    conn.commit()


def get_refresh_run(conn: sqlite3.Connection, run_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM refresh_runs WHERE run_id = ?", (run_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["sources_json"] = json.loads(d["sources_json"] or "{}")
    return d


def log_apify_run(conn: sqlite3.Connection, results: int, estimated_cost_usd: float) -> None:
    """Record one Apify run's cost, used to track spending against the monthly budget cap."""
    conn.execute(
        "INSERT INTO apify_runs (ran_at, results, estimated_cost_usd) VALUES (?, ?, ?)",
        (_now(), results, estimated_cost_usd),
    )
    conn.commit()


def get_apify_spend_this_month(conn: sqlite3.Connection) -> float:
    """
    Sum of estimated Apify costs for the current calendar month.

    We match on the ISO timestamp's "YYYY-MM" prefix with a SQL LIKE, which
    is a simple way to filter by month without needing SQLite's date
    functions.
    """
    month_prefix = datetime.now(timezone.utc).strftime("%Y-%m")
    row = conn.execute(
        "SELECT COALESCE(SUM(estimated_cost_usd), 0) AS total FROM apify_runs WHERE ran_at LIKE ?",
        (f"{month_prefix}%",),
    ).fetchone()
    return float(row["total"])


def get_last_apify_run(conn: sqlite3.Connection) -> Optional[str]:
    """When Apify/LinkedIn last ran, used to enforce the cooldown period."""
    row = conn.execute("SELECT ran_at FROM apify_runs ORDER BY ran_at DESC LIMIT 1").fetchone()
    return row["ran_at"] if row else None
