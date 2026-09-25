"""
Flask server: the local web app itself.

Run with `python app.py`. This starts a server on http://127.0.0.1:8000
(localhost only — see the spec's security note in §10) and opens it in
your browser automatically. It's intentionally a thin layer: all the real
work (fetching, filtering, deduping, categorising) lives in jobboard/, and
this file's only job is to expose that over HTTP for static/index.html to
talk to.

Routes:
  GET  /                    the page itself (static/index.html)
  GET  /api/jobs            every active job, as JSON
  GET  /api/stats           header stats + category counts + Apify budget status
  POST /api/refresh         start a refresh in the background, return immediately
  GET  /api/refresh/status  poll the most recently started refresh's progress
"""
from __future__ import annotations

import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import bleach
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

from jobboard import budget, db
from jobboard.categorise import Categoriser
from jobboard.refresh import run_refresh
from jobboard.sources.base import PROJECT_ROOT, load_yaml

# Must happen before any source reads os.getenv() for its API key —
# load_dotenv() reads .env into the process environment; without this
# call, python-dotenv being installed does nothing on its own and every
# source would see its key as missing even with .env filled in.
load_dotenv(PROJECT_ROOT / ".env")

HOST = "127.0.0.1"  # localhost only — never expose this to the network
PORT = 8000
DB_PATH = PROJECT_ROOT / "data" / "jobs.db"

# The allowlist of HTML tags/attributes we let through into the browser.
# Every description_html value goes through this before the API ever
# returns it — sources give us "safe-ish" HTML at best, and this is the
# one place that actually enforces safety (spec §10's security note).
ALLOWED_TAGS = [
    "p", "br", "ul", "ol", "li", "strong", "b", "em", "i", "a",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "span",
]
ALLOWED_ATTRIBUTES = {"a": ["href", "target", "rel"]}


def sanitize_html(raw_html: str) -> str:
    return bleach.clean(
        raw_html or "", tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES, strip=True
    )


app = Flask(__name__, static_folder="static", static_url_path="")

# All the shared, refresh-related state the routes below need. This is a
# single-user local app, so a couple of module-level globals (guarded by
# one lock) is simpler than anything fancier, and matches the "no user
# accounts" scope in the spec.
_conn = db.get_db(DB_PATH)
_settings = load_yaml("settings.yaml")
_categoriser = Categoriser()
_refresh_lock = threading.Lock()
_refresh_in_progress = False
_current_run_id: Optional[str] = None


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/jobs")
def api_jobs():
    jobs = db.get_active_jobs(_conn, max_age_days=_settings.get("max_posted_age_days"))
    payload = []
    for job in jobs:
        d = job.to_dict()
        d["description_html"] = sanitize_html(d["description_html"])
        payload.append(d)
    return jsonify(payload)


@app.route("/api/stats")
def api_stats():
    jobs = db.get_active_jobs(_conn, max_age_days=_settings.get("max_posted_age_days"))
    today = datetime.now(timezone.utc).date().isoformat()
    new_today = sum(1 for job in jobs if (job.first_seen_at or "")[:10] == today)

    category_counts: dict[str, int] = {}
    for job in jobs:
        category_counts[job.category] = category_counts.get(job.category, 0) + 1

    last_updated = max((job.last_seen_at for job in jobs), default=None)

    return jsonify(
        {
            "total_active": len(jobs),
            "new_today": new_today,
            "last_updated": last_updated,
            "categories": [
                {"name": name, "count": count} for name, count in sorted(category_counts.items())
            ],
            "apify": budget.get_status(_conn, _settings),
        }
    )


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    global _refresh_in_progress, _current_run_id

    if not _refresh_lock.acquire(blocking=False):
        return jsonify({"error": "a refresh is already running"}), 409

    force_linkedin = bool((request.get_json(silent=True) or {}).get("force_linkedin", False))
    run_id = None

    def _background_refresh():
        global _refresh_in_progress
        try:
            run_refresh(_conn, _settings, _categoriser, force_linkedin=force_linkedin, run_id=run_id)
        finally:
            _refresh_in_progress = False
            _refresh_lock.release()

    try:
        import uuid

        run_id = str(uuid.uuid4())
        _current_run_id = run_id
        _refresh_in_progress = True
        thread = threading.Thread(target=_background_refresh, daemon=True)
        thread.start()
    except Exception:
        _refresh_in_progress = False
        _refresh_lock.release()
        raise

    return jsonify({"run_id": run_id, "status": "started"}), 202


@app.route("/api/refresh/status")
def api_refresh_status():
    if _current_run_id is None:
        return jsonify({"status": "idle"})

    run = db.get_refresh_run(_conn, _current_run_id)
    if run is None:
        return jsonify({"status": "idle"})

    return jsonify(
        {
            "run_id": run["run_id"],
            "status": run["status"],
            "started_at": run["started_at"],
            "finished_at": run["finished_at"],
            "sources": run["sources_json"],
        }
    )


def _open_browser_when_ready() -> None:
    webbrowser.open(f"http://{HOST}:{PORT}/")


if __name__ == "__main__":
    threading.Timer(1.0, _open_browser_when_ready).start()
    # threaded=True so /api/refresh/status can be polled while a refresh
    # is running in its own background thread.
    app.run(host=HOST, port=PORT, threaded=True)
