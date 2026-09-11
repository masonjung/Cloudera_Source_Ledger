"""Reading the record back — `data/present.py`.

The rendering rules are the record's rules, one layer later: NULL is not empty, and
nothing that reads the store may write to it. Both are asserted here rather than
trusted to the notebook cell that calls them, which is why they left the notebook.
"""

import csv
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


# --- the record as a file ----------------------------------------------------
#
# The flattening moved here from scripts/cli.py, which still re-exports it: the
# shape is a rule about the record, and a rule two interfaces each implement is a
# rule until one of them drifts. tests/test_cli.py asserts the terminal surface;
# these assert the rule itself.

def test_export_writes_one_row_per_url_not_per_search(recorded, tmp_path):
    """Three URLs across two searches is three rows. A search-grained file with a
    nested URL list cannot be sorted by domain, which is the one thing a reviewer
    opens it to do."""
    path, rows = present.export(tmp_path / "appendix.csv")

    assert len(rows) == 3
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 4  # + header


def test_export_spells_null_out_because_csv_has_only_one_empty_cell(recorded, tmp_path):
    """The distinction section 4 renders as n/a and any has to survive into the
    file, or the export claims a filter that never ran."""
    path, _ = present.export(tmp_path / "appendix.csv")
    rows = list(csv.DictReader(path.open(encoding="utf-8")))

    unsupported = next(r for r in rows if r["query"] == "semaglutide pharmacovigilance")
    unused = next(r for r in rows if r["query"] == "glp-1 adverse events")

    assert unsupported["region"] == "NULL"   # this corpus has no such option
    assert unused["timelimit"] == ""         # it has one, and this search skipped it


def test_export_carries_every_provenance_column(recorded, tmp_path):
    """A column missing from the appendix is provenance the reviewer cannot check."""
    path, _ = present.export(tmp_path / "appendix.csv")
    header = next(csv.reader(path.open(encoding="utf-8")))

    assert header == list(present.EXPORT_COLUMNS)


def test_export_creates_the_directory_it_writes_into(recorded, tmp_path):
    """data/exports/ is gitignored, so a fresh clone does not have it and the
    first Run All would otherwise fail on the deliverable cell."""
    path, _ = present.export(tmp_path / "never-made" / "appendix.csv")

    assert path.is_file()


def test_export_overwrites_rather_than_appends(recorded, tmp_path):
    """Re-running a cell must leave the record as it stands now, not twice over."""
    path, first = present.export(tmp_path / "appendix.csv")
    _, second = present.export(tmp_path / "appendix.csv")

    assert len(second) == len(first)
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == len(first) + 1


def test_export_defaults_under_data_not_the_working_directory():
    """A notebook's cwd depends on how its kernel was started, so the default path
    is pinned to the layer directory. Asserted on the path alone — this test must
    not write into the developer's real data/exports/."""
    assert present.EXPORTS == present.db.HERE / "exports"
    assert present.EXPORTS.name == "exports"


def test_export_does_not_stop_at_the_list_searches_default():
    """`db.list_searches()` defaults to 50 rows because it backs a preview. An
    export is a deliverable and silently truncating one is how an appendix ends up
    missing the searches that mattered."""
    assert present.EXPORT_LIMIT > 50


def test_export_keeps_rank_as_stored(recorded, tmp_path):
    """0-based, as the store has it. Renumbering here would make the export a
    second source of truth for rank."""
    path, _ = present.export(tmp_path / "appendix.csv")
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    positions = [r["position"] for r in rows if r["query"] == "semaglutide pharmacovigilance"]

    assert positions == ["0", "1"]


def test_head_shows_the_top_of_the_file(recorded, tmp_path):
    path, _ = present.export(tmp_path / "appendix.csv")

    assert present.head(path, lines=2) == \
        path.read_text(encoding="utf-8").splitlines()[:2]
