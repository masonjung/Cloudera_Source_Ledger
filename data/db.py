"""Source Ledger persistence — SQLite storage for searches and their result URLs.

The database is a single SQLite file, `source_ledger.db` in this directory by default
(set the SOURCE_LEDGER_DB env var to move it). The schema lives in schema.sql.
Only links are stored, never page content.
"""

import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("SOURCE_LEDGER_DB") or HERE / "source_ledger.db")

_SCHEMA = (HERE / "schema.sql").read_text(encoding="utf-8")


@contextmanager
def _db():
    """One connection: transaction committed *and* handle closed on exit.

    `sqlite3`'s own context manager commits or rolls back the transaction but
    leaves the connection open, so `with sqlite3.connect(...)` leaks a handle
    until garbage collection reclaims it. Wrapping it here keeps every existing
    `with _db() as conn:` call site unchanged while making the close explicit —
    which matters most on Windows, where a lingering handle can block the file
    operations backup() and clear_all() depend on.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    with closing(conn), conn:
        yield conn


def init_db():
    with _db() as conn:
        conn.executescript(_SCHEMA)
        # Migrate DBs created before the settings columns existed.
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(searches)")}
        for col, sqltype in (("region", "TEXT"), ("safesearch", "TEXT"), ("timelimit", "TEXT"),
                             ("backend", "TEXT"), ("max_results", "INTEGER"),
                             ("provider", "TEXT")):
            if col not in cols:
                conn.execute(f"ALTER TABLE searches ADD COLUMN {col} {sqltype}")
        url_cols = {row["name"] for row in conn.execute("PRAGMA table_info(search_urls)")}
        if "provider" not in url_cols:
            conn.execute("ALTER TABLE search_urls ADD COLUMN provider TEXT")
            # Backfill from the parent search. Rows predating the provider seam
            # leave it NULL on both tables, which is the honest answer.
            conn.execute(
                "UPDATE search_urls SET provider ="
                " (SELECT provider FROM searches WHERE searches.id = search_urls.search_id)"
            )


def save_search(query, urls, *, provider=None, region=None, safesearch=None,
                timelimit=None, backend=None, max_results=None):
    """Persist one search and its ordered URLs.

    On the settings arguments, None and "" mean different things and callers must
    keep them apart:

    * ``None`` — this provider does not support the option. Stored NULL.
    * ``""``   — supported, left unset by the user (ddgs with "any time").
    * a value  — supported and applied.

    Collapsing the first two would record a filter that never ran, which is the one
    thing a governed, reproducible search record must not do.
    """
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO searches"
            " (query, created_at, provider, region, safesearch, timelimit, backend, max_results)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (query, datetime.now(timezone.utc).isoformat(),
             provider, region, safesearch, timelimit, backend, max_results),
        )
        conn.executemany(
            "INSERT INTO search_urls (search_id, position, url, provider)"
            " VALUES (?, ?, ?, ?)",
            [(cur.lastrowid, i, url, provider) for i, url in enumerate(urls)],
        )
        return cur.lastrowid


def backup(dest):
    """Write a consistent snapshot of the dev store to `dest`. Returns the path.

    Uses SQLite's online backup API rather than a file copy, because the app
    holds the database open while it runs: copying the file mid-write can capture
    a torn page, and that corruption stays invisible until the backup is the only
    copy left. The API takes a proper read lock and restarts if the source
    changes underneath it, so `make dev` can keep serving throughout.

    Refuses to overwrite. A backup command that silently replaces the previous
    snapshot is one keystroke away from destroying the history it exists to keep,
    so a new name per run is the caller's job — see data/backup.py.
    """
    dest = Path(dest)
    if not DB_PATH.exists():
        # sqlite3.connect() would happily create an empty file here and the
        # backup would "succeed" with zero rows, which is worse than failing.
        raise FileNotFoundError(DB_PATH)
    if dest.exists():
        raise FileExistsError(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    ok = False
    try:
        with _db() as source, closing(sqlite3.connect(dest)) as target:
            source.backup(target)
        ok = True
    finally:
        if not ok:
            # A half-written file must not survive looking like a backup — the
            # next run would refuse to overwrite it and the real one never lands.
            dest.unlink(missing_ok=True)
    return dest


def list_searches(limit=50):
    """Recent searches, newest first, each with its ordered URLs."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, query, created_at, provider, region, safesearch, timelimit,"
            " backend, max_results"
            " FROM searches ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        # dict(r) carries exactly the columns selected above, so adding one to
        # the SELECT no longer needs a matching edit here.
        return [{**dict(r), "urls": [u["url"] for u in conn.execute(
            "SELECT url FROM search_urls WHERE search_id = ? ORDER BY position", (r["id"],)
        )]} for r in rows]


def stats():
    with _db() as conn:
        searches, urls = conn.execute(
            "SELECT (SELECT COUNT(*) FROM searches),"
            " (SELECT COUNT(*) FROM search_urls)"
        ).fetchone()
        return {"searches": searches, "urls": urls}


def delete_search(search_id):
    with _db() as conn:
        conn.execute("DELETE FROM searches WHERE id = ?", (search_id,))


def clear_all():
    with _db() as conn:
        conn.execute("DELETE FROM search_urls")
        conn.execute("DELETE FROM searches")


def dedupe_urls():
    """Delete duplicate URLs across searches, keeping the earliest occurrence.

    Searches left with no URLs are removed too. Returns the number of URL rows deleted.

    Note this is lossy for provenance now that URLs carry a `provider`: a page
    returned by both a web search and Wikipedia keeps only the earlier row, so
    the other provider's sighting is discarded. That is fine locally — this is
    a dev-store convenience, and the lakehouse path keeps every sighting and
    aggregates them into `curated_urls.providers` instead of deleting. Expect
    the collision rate to rise as more providers are used, since encyclopedia
    and scholarly results do surface on the open web too.
    """
    with _db() as conn:
        cur = conn.execute(
            "DELETE FROM search_urls WHERE id NOT IN"
            " (SELECT MIN(id) FROM search_urls GROUP BY url)"
        )
        conn.execute(
            "DELETE FROM searches WHERE id NOT IN (SELECT DISTINCT search_id FROM search_urls)"
        )
        return cur.rowcount
