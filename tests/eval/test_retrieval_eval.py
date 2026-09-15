"""Retrieval eval harness — the Harden gate for `retrieval/source_ledger.py`.

Two tiers:

* **Contract tests** (always run) assert the guarantees every caller depends on,
  against a stubbed engine. Fast, deterministic, safe in CI.
* **Live tests** (`--live` only) call real search engines to measure availability
  and check the fallback chain earns its place. Off by default — CI must never
  fail because a provider is rate-limiting.

```bash
make test                 # contract tier only
pytest tests/eval --live  # add the live tier
```

The numbers the model card records come from `retrieval/notebooks/eval.ipynb`.
This file asserts the properties; the notebook measures them.
"""

import time

import pytest

import source_ledger

ENGINES = ["duckduckgo", "yahoo", "startpage", "yandex"]

# Every corpus the retrieval layer implements. The form offers all four today, but this
# list tracks the registry rather than the form, because the provider is what these tests
# measure. These are an axis, not links in the `backend` chain: a web-search miss must not
# fall through to arXiv and answer with preprints.
PROVIDERS = ["ddgs", "wikipedia", "openalex", "arxiv"]

# A search that takes longer than this is unusable behind a synchronous form
# post, whatever it returns.
LATENCY_BUDGET_S = 20.0


def assert_retrieval_contract(urls, max_results):
    """Every guarantee `text_to_urls` makes, in one place.

    Both tiers call this, so a live run checks the same invariants as CI — the
    live tier differs only in whether a real engine produced the input.
    """
    assert isinstance(urls, list), "must return a list, never None"
    assert all(isinstance(u, str) for u in urls), "every element is a string"
    assert all(u for u in urls), "no empty strings"
    assert len(urls) == len(set(urls)), "deduplicated"
    assert len(urls) <= max_results, f"at most max_results ({max_results})"
    assert all(u.startswith(("http://", "https://")) for u in urls), \
        "absolute http(s) URLs only"


# --- Contract tier (always runs) -------------------------------------------

class TestRetrievalContract:
    @pytest.fixture
    def stub(self, monkeypatch):
        """Install a canned ddgs response.

        See `provider_stub` for the seam that covers every provider; this one stays
        ddgs-shaped because the contract cases below are written against real ddgs
        payloads, including the `href`/`url` rename and the entries missing a URL.
        """
        def _install(results):
            class Fake:
                # `source_ledger` configures the client with a timeout and proxy, so a
                # stand-in has to accept construction arguments.
                def __init__(self, **kwargs):
                    pass

                def text(self, query, **kwargs):
                    return results
            monkeypatch.setattr(source_ledger, "DDGS", Fake)
        return _install

    @pytest.fixture
    def provider_stub(self, monkeypatch):
        """Replace a provider in the registry with one returning fixed URLs.

        Patching the registry rather than a provider's internals is what lets the
        contract tier cover every corpus through one mechanism — and what stops a
        provider added later from quietly escaping it.
        """
        def _install(provider, urls):
            monkeypatch.setitem(source_ledger.REGISTRY, provider, lambda text, **kw: urls)
        return _install

    def test_wellformed_response_satisfies_the_contract(self, stub, fake_results):
        stub(fake_results)
        assert_retrieval_contract(source_ledger.text_to_urls("cdp", max_results=10), 10)

    def test_duplicate_heavy_response_still_satisfies_it(self, stub):
        stub([{"href": "https://example.com/same"}] * 20)
        urls = source_ledger.text_to_urls("cdp", max_results=10)

        assert_retrieval_contract(urls, 10)
        assert len(urls) == 1

    def test_empty_response_satisfies_it(self, stub):
        stub([])
        assert_retrieval_contract(source_ledger.text_to_urls("cdp", max_results=10), 10)

    def test_null_response_satisfies_it(self, stub):
        """ddgs can return None rather than an empty list."""
        stub(None)
        assert_retrieval_contract(source_ledger.text_to_urls("cdp", max_results=10), 10)

    def test_malformed_entries_are_filtered_out(self, stub):
        stub([
            {"href": "https://example.com/good"},
            {"title": "no href at all"},
            {"href": None},
            {"href": ""},
        ])
        assert_retrieval_contract(source_ledger.text_to_urls("cdp", max_results=10), 10)

    def test_every_ui_engine_is_accepted(self, stub):
        """The four engines the UI exposes must all be valid `backend` values.
        A typo here means a UI toggle that silently returns nothing."""
        stub([{"href": "https://example.com/1"}])
        for engine in ENGINES:
            assert source_ledger.text_to_urls("cdp", backend=engine) == ["https://example.com/1"]

    def test_full_chain_is_accepted(self, stub):
        stub([{"href": "https://example.com/1"}])
        assert source_ledger.text_to_urls("cdp", backend=",".join(ENGINES))

    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_every_ui_provider_satisfies_the_contract(self, provider_stub, provider):
        """The contract is a property of retrieval, not of ddgs. Every corpus makes
        the same six promises, so the Serve layer and the lakehouse can treat their
        output identically."""
        provider_stub(provider, ["https://example.com/1", "https://example.com/1",
                                 "https://example.com/2"])
        urls = source_ledger.text_to_urls("cdp", provider=provider, max_results=10)

        assert_retrieval_contract(urls, 10)
        assert urls == ["https://example.com/1", "https://example.com/2"]

    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_max_results_is_capped_for_every_provider(self, provider_stub, provider):
        """ddgs enforces its own ceiling; an API asked for 3 may still return more.
        The cap is re-applied centrally so the guarantee does not depend on each
        provider being well-behaved."""
        provider_stub(provider, [f"https://example.com/{i}" for i in range(20)])
        urls = source_ledger.text_to_urls("cdp", provider=provider, max_results=3)

        assert_retrieval_contract(urls, 3)


# --- Live tier (--live only) -----------------------------------------------

@pytest.mark.live
class TestLiveRetrieval:
    """Calls real engines. Each test pauses afterwards to stay a good citizen."""

    COOLDOWN_S = 2

    @pytest.fixture(autouse=True)
    def cooldown(self):
        yield
        time.sleep(self.COOLDOWN_S)

    def test_default_engine_returns_results(self):
        """The one live check worth gating a release on: the default engine, on
        an easy query, returns something. If this fails the accelerator is down
        for every user regardless of what else passes."""
        urls = source_ledger.text_to_urls("cloudera cdp documentation", max_results=10)

        assert_retrieval_contract(urls, 10)
        assert urls, "default engine (duckduckgo) returned nothing"

    def test_within_the_latency_budget(self):
        start = time.monotonic()
        source_ledger.text_to_urls("iceberg table format", max_results=10)
        elapsed = time.monotonic() - start

        assert elapsed < LATENCY_BUDGET_S, \
            f"retrieval took {elapsed:.1f}s, budget is {LATENCY_BUDGET_S}s"

    def test_max_results_is_respected_by_real_engines(self):
        urls = source_ledger.text_to_urls("apache iceberg", max_results=5)
        assert_retrieval_contract(urls, 5)

    def test_fallback_chain_is_at_least_as_good_as_one_engine(self):
        """The chain exists so a throttled engine cannot fail a search. If it
        ever returns *fewer* results than the default alone, the ordering is
        wrong — see the overlap analysis in retrieval/notebooks/eval.ipynb."""
        query = "cloudera data engineering spark"
        solo = source_ledger.text_to_urls(query, max_results=10)
        time.sleep(self.COOLDOWN_S)
        chained = source_ledger.text_to_urls(query, max_results=10, backend=",".join(ENGINES))

        assert_retrieval_contract(chained, 10)
        assert len(chained) >= len(solo)

    @pytest.mark.parametrize("engine", ENGINES)
    def test_engine_availability(self, engine):
        """Per-engine availability. Expected to be flaky by design — a failure
        here is a measurement, not necessarily a defect. Record it in the model
        card rather than muting the test."""
        urls = source_ledger.text_to_urls("apache iceberg", max_results=10, backend=engine)

        assert_retrieval_contract(urls, 10)
        if not urls:
            pytest.skip(f"{engine} returned nothing — throttled or blocked")

    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_provider_availability(self, provider):
        """The measurement this whole change was made for.

        The three API providers are expected to answer from anywhere, including a
        datacenter IP, while `ddgs` is the one that may be blocked. If that pattern
        ever inverts, the argument for the API providers has weakened and belongs
        in the model card. A skip here is a recorded observation, not a pass."""
        urls = source_ledger.text_to_urls("apache iceberg", max_results=10, provider=provider)

        assert_retrieval_contract(urls, 10)
        if not urls:
            pytest.skip(f"{provider} returned nothing — throttled or blocked")

    def test_wikipedia_region_selects_the_language_edition(self):
        """`region` has to mean something real per provider, or the per-provider
        controls are decoration. Korean must return the Korean edition."""
        urls = source_ledger.text_to_urls("아파치 아이스버그", max_results=5,
                                  provider="wikipedia", region="kr-kr")

        assert_retrieval_contract(urls, 5)
        if not urls:
            pytest.skip("wikipedia returned nothing — throttled or blocked")
        assert all(url.startswith("https://ko.wikipedia.org/") for url in urls)
