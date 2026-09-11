"""Reading the record back — `data/present.py`.

The rendering rules are the record's rules, one layer later: NULL is not empty, and
nothing that reads the store may write to it. Both are asserted here rather than
trusted to the notebook cell that calls them, which is why they left the notebook.
"""

import sqlite3

import present
import pytest


@pytest.fixture
def recorded(temp_db):
    """Two searches: one that had an option and skipped it, one that has none."""
    temp_db.save_search("glp-1 adverse events", ["https://a.example/1"],
                        provider="ddgs", region="wt-wt", safesearch="moderate",
                        timelimit="", backend="duckduckgo", max_results=10)
    temp_db.save_search("semaglutide pharmacovigilance",
                        ["https://b.example/1", "https://b.example/2"],
                        provider="openalex", region=None, safesearch=None,
                        timelimit="y", backend=None, max_results=10)
    return temp_db


def test_ask_returns_the_columns_it_was_asked_for(recorded):
    columns, rows = present.ask(
        "SELECT query, timelimit FROM searches ORDER BY id")

    assert columns == ["query", "timelimit"]
    assert rows == [("glp-1 adverse events", ""),
                    ("semaglutide pharmacovigilance", "y")]


def test_the_record_cannot_be_written_through_ask(recorded):
    """A cell for interrogating a governed store must not be able to alter it, and
    the guarantee is the read-only URI rather than the prose above the cell."""
    with pytest.raises(sqlite3.Error, match="readonly"):
        present.ask("DELETE FROM searches")

    assert present.ask("SELECT COUNT(*) FROM searches")[1] == [(2,)]


def test_a_path_with_spaces_in_it_is_still_a_valid_uri(recorded, tmp_path,
                                                       monkeypatch):
    """`C:\\Users\\Given Name\\...` is the normal case on Windows, and a URI built by
    concatenation rather than by Path.as_uri() breaks on the space."""
    spaced = tmp_path / "a directory with spaces"
    spaced.mkdir()
    monkeypatch.setattr(present.db, "DB_PATH", spaced / "record.db")
    present.db.init_db()

    assert present.ask("SELECT COUNT(*) FROM searches")[1] == [(0,)]


def test_there_is_no_store_before_anything_is_recorded(temp_db, monkeypatch,
                                                       tmp_path):
    """Querying a database that does not exist raises "unable to open database
    file", which is true and useless three cells into a quickstart."""
    monkeypatch.setattr(present.db, "DB_PATH", tmp_path / "never-written.db")

    assert present.store() is None


def test_null_and_unset_do_not_render_alike():
    """The distinction the record exists to keep: NULL means the provider has no
    such option, "" means it has one this search did not use."""
    assert present.cell(None) != present.cell("")
    assert "n/a" in present.cell(None)
    assert "any" in present.cell("")


def test_a_value_renders_as_itself():
    assert present.cell(10) == "10"
    assert present.cell("duckduckgo,brave") == "duckduckgo,brave"


def test_query_text_is_escaped():
    """Recorded query text is whatever someone typed, and it goes into markup."""
    markup = present.table(["query"], [("<script>alert(1)</script>",)])

    assert "<script>" not in markup
    assert "&lt;script&gt;" in markup


def test_searches_shows_every_option_the_record_carries(recorded):
    """The table is the deliverable: a column missing here is provenance the
    reader has to go and find in SQL."""
    columns, _ = present.ask(present.SEARCHES, (10,))

    assert set(columns) >= {"query", "provider", "region", "safesearch",
                            "timelimit", "backend", "max_results", "urls"}


def test_searches_counts_the_urls_of_each_search(recorded):
    columns, rows = present.ask(present.SEARCHES, (10,))
    urls = {row[columns.index("query")]: row[columns.index("urls")] for row in rows}

    assert urls == {"glp-1 adverse events": 1, "semaglutide pharmacovigilance": 2}


def test_searches_is_newest_first(recorded):
    markup = present.searches(limit=10)

    assert markup.index("semaglutide") < markup.index("glp-1")
