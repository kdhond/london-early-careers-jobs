"""SQLite schema, upserts, queries and refresh-run logging."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from jobboard.models import ApplyLink, Job

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

CREATE INDEX IF NOT EXISTS idx_jobs_active ON jobs(is_active);
CREATE INDEX IF NOT EXISTS idx_jobs_company_norm ON jobs(company_normalised);
CREATE INDEX IF NOT EXISTS idx_jobs_posted_at ON jobs(posted_at);

CREATE TABLE IF NOT EXISTS companies (
    company_normalised TEXT PRIMARY KEY,
    website TEXT,
    logo TEXT
);

CREATE TABLE IF NOT EXISTS refresh_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT DEFAULT 'running',
    sources_json TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS apify_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at TEXT NOT NULL,
    results INTEGER NOT NULL,
    estimated_cost_usd REAL NOT NULL
);
"""


def get_db(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_job(row: sqlite3.Row) -> Job:
    d = dict(row)
    apply_links = [ApplyLink(**l) for l in json.loads(d.pop("apply_links") or "[]")]
    requirements = json.loads(d.pop("requirements") or "[]")
    sources = json.loads(d.pop("sources") or "[]")
    d.pop("missed_refreshes", None)
    d["description_is_full"] = bool(d["description_is_full"])
    d["posted_at_estimated"] = bool(d["posted_at_estimated"])
    d["is_active"] = bool(d["is_active"])
    return Job(apply_links=apply_links, requirements=requirements, sources=sources, **d)


def upsert_job(conn: sqlite3.Connection, job: Job) -> None:
    now = _now()
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
    """Bump missed_refreshes for jobs not seen this run; deactivate past the threshold."""
    seen_ids = set(seen_ids)
    rows = conn.execute("SELECT id, missed_refreshes FROM jobs WHERE is_active = 1").fetchall()
    deactivated = 0
    for row in rows:
        if row["id"] in seen_ids:
            continue
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
    rows = conn.execute("SELECT * FROM jobs WHERE is_active = 1").fetchall()
    return [_row_to_job(r) for r in rows]


def get_job(conn: sqlite3.Connection, job_id: str) -> Optional[Job]:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def get_company_website(conn: sqlite3.Connection, company_normalised: str) -> Optional[dict]:
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
    conn.execute(
        "INSERT INTO refresh_runs (run_id, started_at, status) VALUES (?, ?, 'running')",
        (run_id, _now()),
    )
    conn.commit()


def update_refresh_run(
    conn: sqlite3.Connection, run_id: str, sources_status: dict, status: Optional[str] = None
) -> None:
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
    conn.execute(
        "INSERT INTO apify_runs (ran_at, results, estimated_cost_usd) VALUES (?, ?, ?)",
        (_now(), results, estimated_cost_usd),
    )
    conn.commit()


def get_apify_spend_this_month(conn: sqlite3.Connection) -> float:
    month_prefix = datetime.now(timezone.utc).strftime("%Y-%m")
    row = conn.execute(
        "SELECT COALESCE(SUM(estimated_cost_usd), 0) AS total FROM apify_runs WHERE ran_at LIKE ?",
        (f"{month_prefix}%",),
    ).fetchone()
    return float(row["total"])


def get_last_apify_run(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute("SELECT ran_at FROM apify_runs ORDER BY ran_at DESC LIMIT 1").fetchone()
    return row["ran_at"] if row else None
