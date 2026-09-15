"""Lakehouse layer — unit tests for `data/db.py`.

Covers the guarantees the rest of the accelerator relies on: rank order survives
a round trip, deletes cascade, the schema migrates in place, and dedupe keeps the
earliest sighting.
"""

import sqlite3

import pytest


def test_save_and_read_back_preserves_rank_order(temp_db):
    urls = [f"https://example.com/{i}" for i in range(5)]
    temp_db.save_search("iceberg maintenance", urls, region="us-en", backend="duckduckgo")

    rows = temp_db.list_searches()
    assert len(rows) == 1
    assert rows[0]["urls"] == urls  # order, not just membership


def test_options_round_trip(temp_db):
    temp_db.save_search(
        "cdp use cases", ["https://example.com/1"],
        provider="ddgs", region="kr-kr", safesearch="off", timelimit="w",
        backend="duckduckgo,yahoo", max_results=25,
    )
    row = temp_db.list_searches()[0]

    assert row["provider"] == "ddgs"
    assert row["region"] == "kr-kr"
    assert row["safesearch"] == "off"
    assert row["timelimit"] == "w"
    assert row["backend"] == "duckduckgo,yahoo"
    assert row["max_results"] == 25


def test_unsupported_and_unset_options_stay_distinct(temp_db):
    """NULL means the provider has no such option; "" means it has one and the
    search did not use it. SQLite will happily store both, and the difference is
    the whole basis of the claim that a saved search is reproducible."""
    temp_db.save_search(
        "iceberg", ["https://example.com/1"],
        provider="wikipedia", region="kr-kr",
        safesearch=None, timelimit=None, backend=None,
    )
    temp_db.save_search(
        "iceberg", ["https://example.com/2"],
        provider="ddgs", region="wt-wt",
        safesearch="moderate", timelimit="", backend="duckduckgo",
    )
    wiki, web = temp_db.list_searches()[1], temp_db.list_searches()[0]

    assert wiki["timelimit"] is None
    assert web["timelimit"] == ""
    assert wiki["timelimit"] != web["timelimit"]


def test_urls_carry_their_provider(temp_db):
    """Per-URL provenance, denormalised from the parent search.

    The join back to `searches` exists today, but `dedupe_urls()` and the Spark
    aggregation both collapse across searches — so a URL that cannot say which
    corpus found it loses that fact permanently at the first aggregation.
    """
    temp_db.save_search("iceberg", ["https://ko.wikipedia.org/wiki/X"],
                        provider="wikipedia")
    temp_db.save_search("iceberg", ["https://arxiv.org/abs/1"], provider="arxiv")

    with sqlite3.connect(temp_db.DB_PATH) as conn:
        rows = dict(conn.execute("SELECT url, provider FROM search_urls").fetchall())

    assert rows["https://ko.wikipedia.org/wiki/X"] == "wikipedia"
    assert rows["https://arxiv.org/abs/1"] == "arxiv"


def test_url_provider_is_null_when_the_search_has_none(temp_db):
    """No provider on the search means none on its URLs — not a guess."""
    temp_db.save_search("legacy", ["https://example.com/1"])

    with sqlite3.connect(temp_db.DB_PATH) as conn:
        provider = conn.execute("SELECT provider FROM search_urls").fetchone()[0]

    assert provider is None


def test_newest_search_first(temp_db):
    temp_db.save_search("first", ["https://example.com/a"])
    temp_db.save_search("second", ["https://example.com/b"])

    assert [r["query"] for r in temp_db.list_searches()] == ["second", "first"]


def test_stats_counts_both_tables(temp_db):
    temp_db.save_search("a", ["https://example.com/1", "https://example.com/2"])
    temp_db.save_search("b", ["https://example.com/3"])

    assert temp_db.stats() == {"searches": 2, "urls": 3}


def test_delete_cascades_to_urls(temp_db):
    """`PRAGMA foreign_keys = ON` is set per connection — SQLite ignores the
    constraint otherwise, and orphaned URL rows would accumulate silently."""
    search_id = temp_db.save_search("doomed", ["https://example.com/1",
                                               "https://example.com/2"])
    temp_db.delete_search(search_id)

    assert temp_db.stats() == {"searches": 0, "urls": 0}


def test_clear_all_empties_both_tables(temp_db):
    temp_db.save_search("a", ["https://example.com/1"])
    temp_db.save_search("b", ["https://example.com/2"])
    temp_db.clear_all()

    assert temp_db.stats() == {"searches": 0, "urls": 0}


# --- dedupe ----------------------------------------------------------------

def test_dedupe_keeps_earliest_occurrence(temp_db):
    shared = "https://example.com/shared"
    first = temp_db.save_search("first", [shared, "https://example.com/only-a"])
    temp_db.save_search("second", [shared, "https://example.com/only-b"])

    removed = temp_db.dedupe_urls()

    assert removed == 1
    rows = {r["id"]: r for r in temp_db.list_searches()}
    assert shared in rows[first]["urls"]        # earliest sighting survives
    assert len(rows) == 2                        # both searches still have URLs


def test_dedupe_removes_searches_left_empty(temp_db):
    """A search whose URLs were all duplicates has nothing left to show, so it
    is removed rather than rendered as an empty row."""
    urls = ["https://example.com/1", "https://example.com/2"]
    temp_db.save_search("original", urls)
    temp_db.save_search("entirely-redundant", urls)

    temp_db.dedupe_urls()

    assert [r["query"] for r in temp_db.list_searches()] == ["original"]


def test_dedupe_is_idempotent(temp_db):
    temp_db.save_search("a", ["https://example.com/x"])
    temp_db.save_search("b", ["https://example.com/x"])

    assert temp_db.dedupe_urls() == 1
    assert temp_db.dedupe_urls() == 0  # nothing left to remove


def test_dedupe_on_clean_data_removes_nothing(temp_db):
    temp_db.save_search("a", ["https://example.com/1", "https://example.com/2"])

    assert temp_db.dedupe_urls() == 0
    assert temp_db.stats() == {"searches": 1, "urls": 2}


# --- schema / migration ----------------------------------------------------

def test_init_db_is_idempotent(temp_db):
    temp_db.save_search("survivor", ["https://example.com/1"])
    temp_db.init_db()  # every statement in schema.sql is IF NOT EXISTS

    assert temp_db.stats() == {"searches": 1, "urls": 1}


def test_migrates_a_pre_options_database(tmp_path, monkeypatch):
    """Databases created before the option columns existed must upgrade in
    place. This is the one migration path that cannot be re-created by hand."""
    import db

    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy)
    conn.executescript("""
        CREATE TABLE searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE search_urls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            search_id INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            url TEXT NOT NULL
        );
        INSERT INTO searches (query, created_at) VALUES ('old', '2020-01-01T00:00:00+00:00');
        INSERT INTO search_urls (search_id, position, url) VALUES (1, 0, 'https://old.example');
    """)
    conn.commit()
    conn.close()

    monkeypatch.setattr(db, "DB_PATH", legacy)
    db.init_db()

    row = db.list_searches()[0]
    assert row["query"] == "old"
    assert row["region"] is None      # column now exists, unpopulated for old rows
    assert row["max_results"] is None
    assert row["provider"] is None    # searches predating the provider seam
    assert row["urls"] == ["https://old.example"]


def test_migration_backfills_url_provider_from_the_parent_search(tmp_path, monkeypatch):
    """A database written after `searches.provider` existed but before
    `search_urls.provider` did. The parent knows the answer, so the backfill is a
    copy rather than a guess — and it is the only chance to do it, since the join
    stops being reliable once URLs are deduplicated across searches."""
    import db

    legacy = tmp_path / "mid-migration.db"
    conn = sqlite3.connect(legacy)
    conn.executescript("""
        CREATE TABLE searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            created_at TEXT NOT NULL,
            region TEXT, safesearch TEXT, timelimit TEXT,
            backend TEXT, max_results INTEGER, provider TEXT
        );
        CREATE TABLE search_urls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            search_id INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            url TEXT NOT NULL
        );
        INSERT INTO searches (query, created_at, provider)
             VALUES ('wiki search', '2026-08-01T00:00:00+00:00', 'wikipedia');
        INSERT INTO searches (query, created_at, provider)
             VALUES ('older', '2020-01-01T00:00:00+00:00', NULL);
        INSERT INTO search_urls (search_id, position, url) VALUES (1, 0, 'https://ko.wikipedia.org/x');
        INSERT INTO search_urls (search_id, position, url) VALUES (2, 0, 'https://old.example');
    """)
    conn.commit()
    conn.close()

    monkeypatch.setattr(db, "DB_PATH", legacy)
    db.init_db()

    with sqlite3.connect(legacy) as check:
        rows = dict(check.execute("SELECT url, provider FROM search_urls").fetchall())

    assert rows["https://ko.wikipedia.org/x"] == "wikipedia"
    assert rows["https://old.example"] is None  # parent had none either


def test_migrates_a_pre_provider_database(tmp_path, monkeypatch):
    """The upgrade real installations will actually take: a database with every
    option column but no `provider`, written before the seam existed."""
    import db

    legacy = tmp_path / "pre-provider.db"
    conn = sqlite3.connect(legacy)
    conn.executescript("""
        CREATE TABLE searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            created_at TEXT NOT NULL,
            region TEXT, safesearch TEXT, timelimit TEXT,
            backend TEXT, max_results INTEGER
        );
        CREATE TABLE search_urls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            search_id INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            url TEXT NOT NULL
        );
        INSERT INTO searches (query, created_at, region, backend, max_results)
             VALUES ('old', '2020-01-01T00:00:00+00:00', 'wt-wt', 'duckduckgo', 10);
        INSERT INTO search_urls (search_id, position, url) VALUES (1, 0, 'https://old.example');
    """)
    conn.commit()
    conn.close()

    monkeypatch.setattr(db, "DB_PATH", legacy)
    db.init_db()

    row = db.list_searches()[0]
    assert row["provider"] is None    # unknowable in hindsight, so not guessed
    assert row["backend"] == "duckduckgo"
    assert row["urls"] == ["https://old.example"]


def test_created_at_is_utc_iso8601(temp_db):
    """The Serve layer parses this with `datetime.fromisoformat` and converts to
    local time for display. Storing local time would corrupt that."""
    from datetime import datetime

    temp_db.save_search("a", ["https://example.com/1"])
    created_at = temp_db.list_searches()[0]["created_at"]
    parsed = datetime.fromisoformat(created_at)

    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_source_ledger_db_env_var_relocates_the_store(tmp_path, monkeypatch):
    """`SOURCE_LEDGER_DB` is how the store moves without touching code — used by CI and
    by anyone running two accelerators side by side."""
    import importlib

    import db

    target = tmp_path / "elsewhere" / "custom.db"
    target.parent.mkdir()
    monkeypatch.setenv("SOURCE_LEDGER_DB", str(target))
    reloaded = importlib.reload(db)
    try:
        reloaded.init_db()
        reloaded.save_search("a", ["https://example.com/1"])
        assert target.exists()
    finally:
        monkeypatch.undo()
        importlib.reload(db)  # restore the module for the rest of the session


@pytest.mark.parametrize("query", [
    "O'Brien's guide to SQL",              # apostrophe — parameterised, not escaped
    'quotes "everywhere" and ; semicolons',
    "'; DROP TABLE searches; --",          # the classic
    "日本語のクエリ",                        # non-ASCII round trip
])
def test_queries_are_parameterised(temp_db, query):
    temp_db.save_search(query, ["https://example.com/1"])

    assert temp_db.list_searches()[0]["query"] == query
    assert temp_db.stats()["searches"] == 1  # table still exists
