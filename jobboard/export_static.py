"""
Static export for the public site.

`python -m jobboard.refresh` still does all the real work (fetch, filter,
dedupe, categorise, persist to data/jobs.db) exactly as before — this file
is the one new step after that: read the active jobs back out and write
them as a single static JSON file, `docs/data/jobs.json`, which is what
`docs/index.html` (served by GitHub Pages) actually fetches. There is no
live backend for the public site — this file is the entire "backend",
run once a day by .github/workflows/refresh.yml.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import bleach

from jobboard import db
from jobboard.sources.base import PROJECT_ROOT, load_yaml

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "jobs.db"
DEFAULT_OUT_PATH = PROJECT_ROOT / "docs" / "data" / "jobs.json"

# Same allowlist as app.py's sanitize_html — kept separate (not imported
# from app.py) so this script has no dependency on Flask at all.
ALLOWED_TAGS = [
    "p", "br", "ul", "ol", "li", "strong", "b", "em", "i", "a",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "span",
]
ALLOWED_ATTRIBUTES = {"a": ["href", "target", "rel"]}


def sanitize_html(raw_html: str) -> str:
    return bleach.clean(
        raw_html or "", tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES, strip=True
    )


def export(db_path: Path = DEFAULT_DB_PATH, out_path: Path = DEFAULT_OUT_PATH) -> int:
    """Read active jobs from `db_path` and write them to `out_path` as static JSON. Returns the job count."""
    settings = load_yaml("settings.yaml")
    conn = db.get_db(db_path)
    jobs = db.get_active_jobs(conn, max_age_days=settings.get("max_posted_age_days"))

    payload_jobs = []
    for job in jobs:
        d = job.to_dict()
        d["description_html"] = sanitize_html(d["description_html"])
        payload_jobs.append(d)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "jobs": payload_jobs,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload), encoding="utf-8")
    return len(payload_jobs)


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    parser = argparse.ArgumentParser(description="Export active jobs to a static JSON file for GitHub Pages.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help=f"Path to the SQLite database file (default: {DEFAULT_DB_PATH}).")
    parser.add_argument("--out", default=str(DEFAULT_OUT_PATH), help=f"Path to write the static JSON file (default: {DEFAULT_OUT_PATH}).")
    args = parser.parse_args()

    count = export(Path(args.db), Path(args.out))
    print(f"Exported {count} active jobs to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
