"""AI layer — unit tests for `retrieval/source_ledger.py`.

Every test stubs `DDGS`. These verify the contract Source Ledger guarantees about search
results, not whether a search engine is up.
"""

import contextlib
import logging

import pytest
from ddgs.exceptions import DDGSException

import source_ledger


class FakeDDGS:
    """Stand-in for `ddgs.DDGS` that records how it was constructed and called."""

    last_call = None
    last_init = None

    def __init__(self, results=None, raises=None, logs=()):
        self._results = results if results is not None else []
        self._raises = raises
        self._logs = logs

    def text(self, query, **kwargs):
        FakeDDGS.last_call = {"query": query, **kwargs}
        # ddgs reports per-engine failures only through its logger, so a fake that
        # wants to exercise that path has to log the way the real library does.
        for engine, error in self._logs:
            logging.getLogger("ddgs.ddgs").info(
                "Error in engine %s: %r", engine, error)
        if self._raises:
            raise self._raises
        return self._results


@pytest.fixture
def patched(monkeypatch):
    """Install a FakeDDGS returning `results`, and hand back the recorded call.

    The constructor takes keyword arguments now — `source_ledger` configures the client
    with a timeout and proxy — so the stand-in must accept and record them.
    """

    def _install(results=None, raises=None, logs=()):
        def _factory(**kwargs):
            FakeDDGS.last_init = kwargs
            return FakeDDGS(results, raises, logs)

        monkeypatch.setattr(source_ledger, "DDGS", _factory)
        FakeDDGS.last_call = None
        FakeDDGS.last_init = None
        return FakeDDGS

    return _install


def test_extracts_urls_in_rank_order(patched, fake_results):
    patched(fake_results)
    urls = source_ledger.text_to_urls("cloudera cdp")

    assert urls[0] == "https://www.cloudera.com/products/cdp.html?utm_source=search"
    assert urls[1] == "https://docs.cloudera.com/cdp/latest/index.html"


def test_accepts_legacy_url_key(patched, fake_results):
    """ddgs renamed its result key from `url` to `href`.

    Supporting both is what keeps a library upgrade from silently returning zero
    results — the failure mode that is indistinguishable from "no matches".
    """
    patched(fake_results)
    urls = source_ledger.text_to_urls("cloudera cdp")

    assert "https://blog.cloudera.com/iceberg-in-cdp/" in urls


def test_deduplicates_exact_repeats(patched, fake_results):
    patched(fake_results)
    urls = source_ledger.text_to_urls("cloudera cdp")

    assert len(urls) == len(set(urls))
    assert len(urls) == 4  # five results, one exact repeat


def test_empty_text_short_circuits(patched):
    """Blank input must not reach a search engine at all."""
    patched([{"href": "https://example.com"}])

    assert source_ledger.text_to_urls("   ") == []
    assert FakeDDGS.last_call is None


def test_query_is_stripped(patched):
    patched([])
    source_ledger.text_to_urls("  cloudera cdp  ")

    assert FakeDDGS.last_call["query"] == "cloudera cdp"


def test_no_results_returns_empty_list(patched):
    """A throttled engine returns nothing. That must be an empty list, not None."""
    patched(None)

    assert source_ledger.text_to_urls("anything") == []


def test_options_are_forwarded(patched):
    patched([])
    source_ledger.text_to_urls(
        "iceberg",
        max_results=25,
        region="kr-kr",
        safesearch="off",
        timelimit="w",
        backend="duckduckgo,yahoo",
    )

    call = FakeDDGS.last_call
    assert call["region"] == "kr-kr"
    assert call["safesearch"] == "off"
    assert call["timelimit"] == "w"
    assert call["backend"] == "duckduckgo,yahoo"


def test_empty_timelimit_normalises_to_none(patched):
    """The Serve layer sends "" for "any time"; ddgs expects None."""
    patched([])
    source_ledger.text_to_urls("iceberg", timelimit="")

    assert FakeDDGS.last_call["timelimit"] is None


def test_results_without_a_url_are_skipped(patched):
    patched([
        {"title": "no link here"},
        {"href": ""},
        {"href": "https://example.com/real"},
    ])

    assert source_ledger.text_to_urls("x") == ["https://example.com/real"]


def test_engine_failure_propagates(patched):
    """Retrieval does not swallow errors — `app/server.py` turns them into a
    visible message, which is only possible if they surface here."""
    patched(raises=RuntimeError("rate limited"))

    with pytest.raises(RuntimeError, match="rate limited"):
        source_ledger.text_to_urls("iceberg")


# --- client configuration ---------------------------------------------------
#
# ddgs defaults to a 5s timeout that is also its cross-engine collection window, and
# it reads only its own DDGS_PROXY. Both defaults fail on the networks a demo
# actually runs on, so both are configured — and that configuration is worth a test,
# because it is invisible from the outside until the moment it matters.

def test_timeout_and_proxy_reach_the_client(patched, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    patched([])
    source_ledger.text_to_urls("iceberg")

    assert FakeDDGS.last_init["timeout"] == source_ledger.DDGS_TIMEOUT_S
    assert FakeDDGS.last_init["proxy"] == "http://proxy.example:8080"


def test_ddgs_proxy_wins_over_the_generic_one(patched, monkeypatch):
    """`DDGS_PROXY` is the more specific instruction, so it takes precedence."""
    monkeypatch.setenv("HTTPS_PROXY", "http://generic.example:8080")
    monkeypatch.setenv("DDGS_PROXY", "http://specific.example:8080")
    patched([])
    source_ledger.text_to_urls("iceberg")

    assert FakeDDGS.last_init["proxy"] == "http://specific.example:8080"


def test_no_proxy_configured_passes_none(patched, monkeypatch):
    for name in ("DDGS_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(name, raising=False)
    patched([])
    source_ledger.text_to_urls("iceberg")

    assert FakeDDGS.last_init["proxy"] is None


@pytest.mark.parametrize("value, expected", [
    ("30", 30.0),
    ("not-a-number", source_ledger.DDGS_TIMEOUT_DEFAULT_S),
    ("0", source_ledger.DDGS_TIMEOUT_DEFAULT_S),
    ("-5", source_ledger.DDGS_TIMEOUT_DEFAULT_S),
    ("", source_ledger.DDGS_TIMEOUT_DEFAULT_S),
])
def test_timeout_env_is_parsed_or_falls_back(monkeypatch, value, expected):
    """A typo in a demo machine's environment must not take the app down at import."""
    monkeypatch.setenv("SOURCE_LEDGER_DDGS_TIMEOUT", value)

    assert source_ledger._ddgs_timeout() == expected


# --- engine breadth ---------------------------------------------------------

def test_asks_for_enough_results_to_query_every_engine(patched):
    """ddgs sizes its worker pool from `max_results`, so asking for the UI default of
    10 queries only two of the four selected engines. See _DDGS_MIN_REQUEST."""
    patched([])
    source_ledger.text_to_urls("iceberg", max_results=10)

    assert FakeDDGS.last_call["max_results"] == source_ledger._DDGS_MIN_REQUEST


def test_a_larger_request_is_left_alone(patched):
    patched([])
    source_ledger.text_to_urls("iceberg", max_results=50)

    assert FakeDDGS.last_call["max_results"] == 50


def test_caller_still_receives_only_max_results(patched):
    """The widened request must never widen what a caller gets back."""
    patched([{"href": f"https://example.com/{n}"} for n in range(40)])

    assert len(source_ledger.text_to_urls("iceberg", max_results=3)) == 3


# --- telling a blocked engine from an empty corpus --------------------------

def test_engine_errors_with_no_results_raise(patched):
    """ddgs reports "every engine failed" as an empty search. Left alone that reaches
    the user as "No results found." — a dead network wearing the face of a real miss."""
    # ddgs logs the exception object itself, so the reason is whatever it stringifies
    # to — that is what reaches the user, and it is why the fake logs one too.
    patched([], logs=[("duckduckgo", RuntimeError("HTTP 403")),
                      ("yahoo", TimeoutError("timed out"))])

    with pytest.raises(source_ledger.EngineError) as caught:
        source_ledger.text_to_urls("iceberg")

    assert caught.value.failures == [("duckduckgo", "HTTP 403"),
                                     ("yahoo", "timed out")]
    assert "duckduckgo: HTTP 403" in str(caught.value)


def test_engine_errors_raised_as_the_empty_exception_still_surface(patched):
    """The same failure, on the path where ddgs raises its empty-search exception."""
    patched(raises=DDGSException("No results found."),
            logs=[("yandex", RuntimeError("blocked"))])

    with pytest.raises(source_ledger.EngineError):
        source_ledger.text_to_urls("iceberg")


def test_a_genuine_miss_is_still_an_empty_list(patched):
    """No engine complained, so nothing was wrong — the corpus simply had nothing."""
    patched(raises=DDGSException("No results found."))

    assert source_ledger.text_to_urls("iceberg") == []


def test_engine_errors_are_ignored_when_results_came_back(patched):
    """One engine failing while another answered is not a failed search."""
    patched([{"href": "https://example.com/real"}],
            logs=[("yahoo", RuntimeError("HTTP 403"))])

    assert source_ledger.text_to_urls("iceberg") == ["https://example.com/real"]


# --- the silent slow-network failure ----------------------------------------
#
# Engines that never finish are dropped into ddgs's `not_done` set and discarded
# without an exception or a log line, so `failures` is empty and the search looks
# like an ordinary miss. The clock is the only evidence left. This is the failure
# that actually loses a demo, and it is the one ddgs helps with least.

def test_an_empty_search_that_used_the_whole_window_is_a_failure(monkeypatch):
    monkeypatch.setattr(source_ledger, "DDGS_TIMEOUT_S", 10.0)

    with pytest.raises(source_ledger.EngineError, match="no answer within 10s"):
        source_ledger._reject_empty([], elapsed=9.5)


def test_an_empty_search_that_returned_promptly_is_a_miss(monkeypatch):
    """A corpus with nothing in it answers fast. Calling that a failure would put a
    red error next to a query that simply had no matches."""
    monkeypatch.setattr(source_ledger, "DDGS_TIMEOUT_S", 10.0)

    assert source_ledger._reject_empty([], elapsed=0.4) is None


def test_a_named_engine_failure_beats_the_timing_heuristic(monkeypatch):
    """What an engine said about itself is always better evidence than the clock."""
    monkeypatch.setattr(source_ledger, "DDGS_TIMEOUT_S", 10.0)

    with pytest.raises(source_ledger.EngineError) as caught:
        source_ledger._reject_empty([("yahoo", "HTTP 403")], elapsed=9.9)

    assert caught.value.failures == [("yahoo", "HTTP 403")]


def test_a_slow_empty_search_surfaces_through_the_public_call(patched, monkeypatch):
    """End to end, not just the helper: a stalled search must not reach the user as
    an empty result set.

    The clock is faked rather than slept through. A real delay would make the test
    slow and — worse — flaky, since a fast stub can beat any threshold small enough
    to be worth waiting for.
    """
    ticks = iter([100.0, 112.0])  # a search that consumed the full 12s window
    monkeypatch.setattr(source_ledger.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(source_ledger, "DDGS_TIMEOUT_S", 12.0)
    patched([])

    with pytest.raises(source_ledger.EngineError, match="no answer within 12s"):
        source_ledger.text_to_urls("iceberg")


@pytest.mark.parametrize("kwargs", [
    {"results": [{"href": "https://example.com"}]},
    {"raises": RuntimeError("boom")},
])
def test_the_log_handler_is_always_detached(patched, kwargs):
    """A handler left attached would accumulate one per search and leak the records
    of every previous one into the next."""
    logger = logging.getLogger("ddgs.ddgs")
    before = (list(logger.handlers), logger.level, logger.propagate)
    patched(kwargs.get("results"), kwargs.get("raises"))

    with contextlib.suppress(RuntimeError):
        source_ledger.text_to_urls("iceberg")

    assert (list(logger.handlers), logger.level, logger.propagate) == before
