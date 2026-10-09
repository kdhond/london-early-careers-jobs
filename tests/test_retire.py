"""db.retire_missing_from_boards: a job that vanished from an employer board we fetched OK is retired immediately."""
from jobboard import db


def _add(conn, make_job, title, source, company="Acme Bio", **kw):
    job = make_job(title=title, company=company, source=source, **kw)
    job.sources = [source] if isinstance(source, str) else source
    db.upsert_job(conn, job)
    return job


def _active_titles(conn):
    return sorted(j.title for j in db.get_active_jobs(conn))


def test_retires_job_gone_from_a_fetched_board(tmp_path, make_job):
    conn = db.get_db(tmp_path / "t.db")
    kept = _add(conn, make_job, "Still Open", "greenhouse")
    gone = _add(conn, make_job, "Taken Down", "greenhouse")
    n = db.retire_missing_from_boards(conn, {"greenhouse": {kept.company_normalised}}, seen_ids=[kept.id], inactive_after_missed=3)
    assert n == 1 and _active_titles(conn) == ["Still Open"]


def test_leaves_alone_companies_whose_board_was_not_fetched(tmp_path, make_job):
    """If a board failed to load, its company isn't in `boards`, so nothing is retired (the slow rule applies)."""
    conn = db.get_db(tmp_path / "t.db")
    _add(conn, make_job, "Maybe Open", "greenhouse")
    assert db.retire_missing_from_boards(conn, {"greenhouse": set()}, seen_ids=[], inactive_after_missed=3) == 0
    assert _active_titles(conn) == ["Maybe Open"]


def test_leaves_multi_source_and_aggregator_rows_alone(tmp_path, make_job):
    conn = db.get_db(tmp_path / "t.db")
    _add(conn, make_job, "Also On Reed", ["greenhouse", "reed"])
    _add(conn, make_job, "Reed Only", "reed")
    co = make_job(company="Acme Bio").company_normalised
    assert db.retire_missing_from_boards(conn, {"greenhouse": {co}}, seen_ids=[], inactive_after_missed=3) == 0
    assert _active_titles(conn) == ["Also On Reed", "Reed Only"]
