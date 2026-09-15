"""The one writer — unit tests for `data/record.py`.

Every interface that produces a search record goes through this module, so the
guarantees the dashboard used to hold alone are asserted here once, against the
implementation rather than against any one caller. The load-bearing one is
`test_save_writes_null_for_every_option_a_provider_does_not_apply`: it is
parametrized from the live support matrix, so a fifth provider added with the
wrong columns fails here rather than in a governed table months later.
"""

import pytest
import record
import source_ledger


@pytest.fixture
def fake_search(monkeypatch):
    """Record what reaches the provider. Nothing here may touch the network."""
    def search(text, **kwargs):
        search.calls.append({"text": text, **kwargs})
        return ["https://example.com/1", "https://example.com/2"]

    search.calls = []
    monkeypatch.setattr(record.source_ledger, "text_to_urls", search)
    return search


# --- normalize -------------------------------------------------------------

@pytest.mark.parametrize("name", ["provider", "timelimit", "safesearch", "region"])
def test_normalize_whitelists_every_option(name):
    """Nothing a caller supplied reaches a search engine unchecked.

    Parametrized from OPTIONS rather than a copy of it, so an option added to the
    whitelist is covered the moment it is offered.
    """
    assert record.normalize(**{name: "evilcorp"})[name] == record.OPTIONS[name][0]


def test_normalize_defaults_every_option_when_nothing_is_supplied():
    assert record.normalize() == {
        "provider": "ddgs",
        "max_results": record.DEFAULT_MAX_RESULTS,
        "region": "wt-wt",
        "safesearch": "moderate",
        "timelimit": "",
        "backend": ",".join(record.OPTIONS["backend"]),
    }


@pytest.mark.parametrize("asked,expected", [(9999, 50), (0, 1), (-3, 1), (25, 25)])
def test_normalize_clamps_max_results(asked, expected):
    assert record.normalize(max_results=asked)["max_results"] == expected


def test_no_engine_selected_means_every_engine():
    """Unticking everything means "ask them all", not "ask the first one" —
    resilience, not coverage. See docs/ARCHITECTURE.md."""
    assert record.normalize(backend=[])["backend"] == ",".join(record.OPTIONS["backend"])


def test_normalize_drops_unknown_engines_and_keeps_order():
    got = record.normalize(backend=["yahoo", "evilcorp", "duckduckgo", "yahoo"])
    assert got["backend"] == "yahoo,duckduckgo"


def test_normalize_accepts_a_comma_string_as_well_as_a_list():
    """The stored form has to round-trip: re-running a recorded search must not
    have to unpack the engine list by hand."""
    assert (record.normalize(backend="yahoo,startpage")["backend"]
            == record.normalize(backend=["yahoo", "startpage"])["backend"]
            == "yahoo,startpage")


# --- search ----------------------------------------------------------------

def test_search_hands_a_provider_none_not_empty_string_for_timelimit(fake_search):
    """"" is the record value for "supported, left unset"; a provider gets None.

    Passing "" through would offer ddgs an empty string as though it were a time
    window. This is the single invariant the split between search() and save()
    exists to protect.
    """
    record.search("iceberg", record.normalize(timelimit=""))

    assert fake_search.calls[0]["timelimit"] is None


def test_search_passes_every_option_through(fake_search):
    """text_to_urls drops what the provider does not apply, reading the same
    matrix save() reads — so this layer passes everything and claims nothing."""
    record.search("iceberg", record.normalize(
        provider="wikipedia", region="kr-kr", safesearch="off",
        timelimit="w", backend=["yahoo"], max_results=7))
    call = fake_search.calls[0]

    assert call["provider"] == "wikipedia"
    assert call["region"] == "kr-kr"
    assert call["safesearch"] == "off"
    assert call["timelimit"] == "w"
    assert call["backend"] == "yahoo"
    assert call["max_results"] == 7


def test_search_does_not_swallow_a_failure(monkeypatch):
    """Callers report; this layer must not turn an outage into an empty result."""
    def boom(text, **kwargs):
        raise source_ledger.EngineError([("duckduckgo", "HTTP 403")])

    monkeypatch.setattr(record.source_ledger, "text_to_urls", boom)

    with pytest.raises(source_ledger.EngineError):
        record.search("iceberg", record.normalize())


# --- save: the NULL rule ---------------------------------------------------

@pytest.mark.parametrize("provider", record.OPTIONS["provider"])
def test_save_writes_null_for_every_option_a_provider_does_not_apply(
        temp_db, provider):
    """The claim the whole accelerator rests on, checked against the live matrix.

    Every toggleable option is deliberately set to a real value, then each column
    is asserted NULL exactly when the provider does not support it. Reading the
    expectation from `source_ledger.supports()` rather than a copy means a provider
    whose support set changes is caught here.
    """
    options = record.normalize(
        provider=provider, region="kr-kr", safesearch="off",
        timelimit="w", backend=["yahoo"])
    record.save("iceberg", ["https://example.com/1"], options)
    row = temp_db.list_searches()[0]
    supported = source_ledger.supports(provider)

    assert row["provider"] == provider
    for name in record.TOGGLEABLE:
        if name in supported:
            assert row[name] == options[name], f"{provider} applied {name}"
        else:
            assert row[name] is None, f"{provider} does not apply {name}"


def test_save_keeps_supported_but_unset_distinct_from_unsupported(temp_db):
    """ddgs supports timelimit and this search did not use one, so the column is
    "" — an empty filter that ran. Wikipedia's is NULL: no such filter exists."""
    record.save("a", ["https://example.com/1"], record.normalize(provider="ddgs"))
    record.save("b", ["https://example.com/1"], record.normalize(provider="wikipedia"))
    wikipedia, ddgs = temp_db.list_searches()

    assert ddgs["timelimit"] == ""
    assert wikipedia["timelimit"] is None


def test_save_records_max_results_as_applied(temp_db):
    record.save("a", ["https://example.com/1"], record.normalize(max_results=9999))

    assert temp_db.list_searches()[0]["max_results"] == 50


# --- run -------------------------------------------------------------------

def test_run_searches_and_records(temp_db, fake_search):
    result = record.run("iceberg", provider="wikipedia")

    assert result["urls"] == ["https://example.com/1", "https://example.com/2"]
    assert result["search_id"] == temp_db.list_searches()[0]["id"]
    assert temp_db.stats() == {"searches": 1, "urls": 2}


def test_run_with_store_false_searches_and_records_nothing(temp_db, fake_search):
    """A caller piping URLs into another command must not have to pollute the
    governed store to do it."""
    result = record.run("iceberg", store=False)

    assert result["urls"]
    assert result["search_id"] is None
    assert temp_db.stats() == {"searches": 0, "urls": 0}


def test_run_saves_nothing_when_there_are_no_results(temp_db, monkeypatch):
    """A row with no URLs is not a search that happened."""
    monkeypatch.setattr(record.source_ledger, "text_to_urls", lambda text, **kw: [])
    result = record.run("iceberg")

    assert result["urls"] == []
    assert result["search_id"] is None
    assert temp_db.stats() == {"searches": 0, "urls": 0}


def test_run_does_not_search_for_empty_text(temp_db, fake_search):
    result = record.run("   ")

    assert result["urls"] == []
    assert fake_search.calls == []


# --- labels ----------------------------------------------------------------

def test_label_falls_back_to_the_bare_id():
    """A provider retired before it was ever labelled still renders as something."""
    assert record.label("ddgs") == "Web"
    assert record.label("evilcorp") == "evilcorp"


def test_every_offered_provider_is_implemented():
    """The whitelist may offer a subset of what retrieval/ implements, never a
    superset — offering a provider with no registry entry would silently search
    the web under another name."""
    assert set(record.OPTIONS["provider"]) <= set(source_ledger.REGISTRY)
