"""The Harden gate for `scripts/cli.py` — the terminal interface.

The CLI writes the same governed rows the dashboard writes, so it has to earn the
same guarantees: the NULL rule, the engine fallback, the clamp. Those are asserted
here against the command line rather than against the form, because an interface
that agrees with the writer in principle and disagrees in its argument parsing
records the same wrong thing either way.

Two things are specific to a terminal and tested nowhere else: URLs go to stdout
while provenance goes to stderr, and every byte of both is ASCII.

No test here touches the network.
"""

import csv
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest
import source_ledger

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def cli():
    """Import `scripts/cli.py`, which is a script rather than a package member."""
    spec = importlib.util.spec_from_file_location(
        "source_ledger_cli", ROOT / "scripts" / "cli.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_search(cli, monkeypatch):
    """Stub retrieval and record what reached it.

    Mandatory, not belt-and-braces: the autouse `no_network` fixture severs
    `providers._get_bytes`, which the ddgs provider does not go through. A test
    that forgot this would make real calls.
    """
    def search(text, **kwargs):
        search.calls.append({"text": text, **kwargs})
        return [f"https://example.com/{i}" for i in range(3)]

    search.calls = []
    monkeypatch.setattr(cli.record.source_ledger, "text_to_urls", search)
    return search


@pytest.fixture
def run(cli, temp_db, fake_search, capsys):
    """Run one command; return (exit code, stdout, stderr)."""
    def invoke(*argv):
        code = cli.main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return invoke


# --- the record the CLI writes ---------------------------------------------

def test_search_records_the_row(run, temp_db):
    code, out, err = run("search", "iceberg", "maintenance")

    assert code == 0
    row = temp_db.list_searches()[0]
    assert row["query"] == "iceberg maintenance"
    assert row["provider"] == "ddgs"
    assert len(row["urls"]) == 3


def test_unsupported_options_are_stored_null_not_as_typed(run, temp_db):
    """The claim, from the command line: Wikipedia applies neither the time window
    nor the safesearch setting that was typed, so neither is recorded as though it
    ran."""
    run("search", "iceberg", "--provider", "wikipedia",
        "--timelimit", "w", "--safesearch", "on", "--region", "kr-kr",
        "--backend", "yahoo")
    row = temp_db.list_searches()[0]

    assert row["region"] == "kr-kr"       # Wikipedia does apply region
    assert row["safesearch"] is None
    assert row["timelimit"] is None
    assert row["backend"] is None


def test_timelimit_any_is_stored_empty_and_reaches_the_provider_as_none(
        run, temp_db, fake_search):
    """"any" is supported-but-unset, which is not the same as unsupported."""
    run("search", "iceberg")

    assert temp_db.list_searches()[0]["timelimit"] == ""
    assert fake_search.calls[0]["timelimit"] is None


def test_max_results_is_clamped(run, fake_search):
    run("search", "a", "-n", "9999")
    run("search", "b", "-n", "0")

    assert fake_search.calls[0]["max_results"] == 50
    assert fake_search.calls[1]["max_results"] == 1


def test_no_backend_given_asks_every_engine(run, cli, fake_search):
    """Omitting --backend means "ask them all", not "ask the first one"."""
    run("search", "a")

    assert fake_search.calls[0]["backend"] == ",".join(cli.record.OPTIONS["backend"])


def test_backend_order_and_uniqueness_are_preserved(run, fake_search):
    run("search", "a", "--backend", "yahoo", "--backend", "duckduckgo",
        "--backend", "yahoo")

    assert fake_search.calls[0]["backend"] == "yahoo,duckduckgo"


def test_no_store_searches_but_records_nothing(run, temp_db):
    code, out, err = run("search", "iceberg", "--no-store")

    assert code == 0
    assert out.splitlines() == [f"https://example.com/{i}" for i in range(3)]
    assert temp_db.stats() == {"searches": 0, "urls": 0}
    assert "not recorded" in err


# --- exit codes ------------------------------------------------------------

def test_zero_results_exits_three_and_saves_nothing(run, cli, temp_db, monkeypatch):
    """3, not 1: the corpus answered and had nothing. A script has to be able to
    tell that from a dead network, which is what 1 means."""
    monkeypatch.setattr(cli.record.source_ledger, "text_to_urls", lambda t, **kw: [])
    code, out, err = run("search", "iceberg")

    assert code == 3
    assert out == ""
    assert "No results" in err
    assert temp_db.stats() == {"searches": 0, "urls": 0}


def test_every_engine_failing_exits_one_and_names_each_one(run, cli, monkeypatch):
    def boom(text, **kwargs):
        raise source_ledger.EngineError([("duckduckgo", "HTTP 403"),
                                       ("yahoo", "timed out")])

    monkeypatch.setattr(cli.record.source_ledger, "text_to_urls", boom)
    code, out, err = run("search", "iceberg")

    assert code == 1
    assert out == ""
    assert "no engine answered" in err
    assert "duckduckgo: HTTP 403" in err
    assert "yahoo: timed out" in err
    assert "doctor" in err  # the next thing to run is named, not left to guess


def test_a_generic_failure_names_the_provider_not_just_the_exception(
        run, cli, monkeypatch):
    """The exception text comes from whichever library made the call and says
    nothing about which corpus was asked."""
    def boom(text, **kwargs):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(cli.record.source_ledger, "text_to_urls", boom)
    code, out, err = run("search", "iceberg", "--provider", "arxiv")

    assert code == 1
    assert "arXiv" in err
    assert "connection reset" in err
    assert "duckduckgo" not in err  # no engine was involved


def test_an_unknown_provider_is_a_usage_error_not_a_coercion(run):
    """The dashboard coerces because a form post is untrusted wire input. A typed
    command is a typo, and silently searching the web instead is worse than a
    usage message."""
    with pytest.raises(SystemExit) as exit_info:
        run("search", "iceberg", "--provider", "evilcorp")

    assert exit_info.value.code == 2


def test_an_empty_query_is_a_usage_error(run):
    code, out, err = run("search", "   ")

    assert code == 2
    assert out == ""


# --- the terminal contract -------------------------------------------------

def test_urls_go_to_stdout_and_provenance_to_stderr(run):
    """`search ... > urls.txt` must leave a file of URLs and nothing else."""
    code, out, err = run("search", "iceberg", "--provider", "wikipedia")

    assert out.splitlines() == [f"https://example.com/{i}" for i in range(3)]
    assert "Source Ledger search" in err
    assert "not applied" in err


def test_quiet_drops_the_provenance_but_keeps_the_urls(run):
    code, out, err = run("search", "iceberg", "--quiet")

    assert out.splitlines() == [f"https://example.com/{i}" for i in range(3)]
    assert err == ""


def test_the_report_names_the_options_this_corpus_ignored(run):
    code, out, err = run("search", "iceberg", "--provider", "openalex")

    assert "not applied" in err
    for ignored in ("region", "safesearch", "backend"):
        assert ignored in err
    assert "recorded NULL" in err


def test_search_json_carries_the_options_that_were_applied(run):
    code, out, err = run("search", "iceberg", "--provider", "wikipedia", "--json")
    payload = json.loads(out)

    assert payload["query"] == "iceberg"
    assert payload["options"]["provider"] == "wikipedia"
    assert payload["search_id"] is not None
    assert len(payload["urls"]) == 3


@pytest.mark.parametrize("argv", [
    ("search", "iceberg"),
    ("search", "iceberg", "--provider", "arxiv"),
    ("list",),
    ("list", "--urls"),
    ("stats",),
    ("export",),
    ("export", "--format", "json"),
])
def test_every_output_path_is_ascii(run, argv):
    """A Windows console is cp1252 by default, and a tool that raises
    UnicodeEncodeError while printing its own success message is not a tool.
    `data/backup.py` documents the same constraint."""
    run("search", "seed")  # so list/export have something to print
    code, out, err = run(*argv)

    out.encode("ascii")
    err.encode("ascii")


# --- reading the record back -----------------------------------------------

def test_list_shows_null_and_unset_differently(run):
    run("search", "iceberg", "--provider", "wikipedia")
    code, out, err = run("list")

    assert "Wikipedia" in out
    assert "timelimit=n/a" in out      # Wikipedia has no time window
    assert "region=wt-wt" in out       # it does have a region, and applied it


def test_list_urls_shows_each_url(run):
    run("search", "iceberg")
    code, out, err = run("list", "--urls")

    assert "https://example.com/0" in out


def test_list_json_is_the_rows_as_stored(run):
    run("search", "iceberg", "--provider", "wikipedia")
    code, out, err = run("list", "--json")
    rows = json.loads(out)

    assert rows[0]["timelimit"] is None      # null survives, unlike in csv
    assert rows[0]["query"] == "iceberg"


def test_stats_reports_the_store(run):
    run("search", "iceberg")
    code, out, err = run("stats")

    assert "searches  1" in out
    assert "urls      3" in out


# --- export ----------------------------------------------------------------

def test_export_csv_has_one_row_per_url_carrying_its_provenance(run, cli):
    """The deliverable is "every source, and how it was found" — a reviewer sorts
    it by domain and filters by provider, neither of which a search-grained file
    with a nested url list allows."""
    run("search", "iceberg", "--provider", "wikipedia")
    code, out, err = run("export")
    rows = list(csv.DictReader(io.StringIO(out)))

    assert [r["url"] for r in rows] == [f"https://example.com/{i}" for i in range(3)]
    assert [r["position"] for r in rows] == ["0", "1", "2"]  # 0-based, as stored
    for row in rows:
        assert row["query"] == "iceberg"
        assert row["provider"] == "wikipedia"
    assert list(rows[0]) == list(cli.EXPORT_COLUMNS)


def test_export_csv_writes_null_for_unsupported_and_empty_for_unset(run):
    """CSV has one empty cell and this record has two meanings for it."""
    run("search", "iceberg", "--provider", "wikipedia")  # no timelimit at all
    run("search", "iceberg", "--provider", "ddgs")       # has one, unused
    code, out, err = run("export")
    rows = list(csv.DictReader(io.StringIO(out)))
    wikipedia = [r for r in rows if r["provider"] == "wikipedia"][0]
    ddgs = [r for r in rows if r["provider"] == "ddgs"][0]

    assert wikipedia["timelimit"] == "NULL"
    assert ddgs["timelimit"] == ""
    assert "NULL =" in err  # the legend, so the file is readable without this test


def test_export_json_preserves_null_versus_empty(run):
    run("search", "iceberg", "--provider", "wikipedia")
    run("search", "iceberg", "--provider", "ddgs")
    code, out, err = run("export", "--format", "json")
    rows = json.loads(out)

    assert [r for r in rows if r["provider"] == "wikipedia"][0]["timelimit"] is None
    assert [r for r in rows if r["provider"] == "ddgs"][0]["timelimit"] == ""


def test_export_is_oldest_first(run):
    """A review appendix reads in the order the searching happened, not backwards."""
    run("search", "first")
    run("search", "second")
    code, out, err = run("export")
    rows = list(csv.DictReader(io.StringIO(out)))

    assert [r["query"] for r in rows][0] == "first"
    assert [r["query"] for r in rows][-1] == "second"


def test_export_to_a_file_writes_it_without_doubling_line_endings(run, tmp_path):
    """csv defaults to \\r\\n and a text stream would translate the \\n again."""
    run("search", "iceberg")
    out_file = tmp_path / "appendix.csv"
    code, out, err = run("export", "--out", str(out_file))

    assert code == 0
    assert "\r\r\n" not in out_file.read_bytes().decode("utf-8")
    assert len(list(csv.DictReader(out_file.open(newline="")))) == 3


# --- delegation ------------------------------------------------------------

def test_backup_delegates_and_writes_one_snapshot(run, cli, tmp_path, monkeypatch):
    # Redirect away from the developer's real backups/ before anything can run,
    # for the same reason conftest does it for the Store button.
    monkeypatch.setattr(cli.backup, "DEFAULT_DIR", tmp_path / "backups")
    run("search", "iceberg")
    code, out, err = run("backup", "--dir", str(tmp_path / "snaps"))

    assert code == 0
    assert len(list((tmp_path / "snaps").glob("*.db"))) == 1


def test_doctor_passes_its_exit_code_through(run, cli, monkeypatch):
    """Including its deliberate 0 when the web is blocked — a blocked engine is a
    measurement of this network, not a failure of the tool."""
    monkeypatch.setattr(cli.doctor, "main", lambda: 0)

    assert run("doctor")[0] == 0
