"""AI layer — the API providers behind `retrieval/providers.py`.

Every provider is exercised through the one HTTP seam, `_get_bytes`. Patching it
stubs the network for all of them at once, which is the same property the suite's
`no_network` guard relies on: if a provider ever reached out some other way, these
tests would not be testing it and CI would quietly start making real calls.

The payloads below are trimmed real responses. Their messiness is the point — the
OpenAlex work with no DOI and the arXiv entry with no HTML link are the shapes that
produce a bare identifier where the retrieval contract demands an absolute URL.
"""

import json
import urllib.error

import providers
import pytest
import source_ledger
from ddgs.exceptions import DDGSException

# Captured at import, before conftest's `no_network` fixture replaces the attribute.
# That is what lets the retry policy *inside* `_get_bytes` be exercised while the
# network stays severed: the fake goes on `_request_once`, one level below it.
_REAL_GET_BYTES = providers._get_bytes

WIKIPEDIA_PAYLOAD = {
    "query": {"search": [
        {"title": "Apache Iceberg"},
        {"title": "Iceberg (disambiguation)"},
        {"title": "Table format"},
    ]},
}

OPENALEX_PAYLOAD = {
    "results": [
        # A DOI outranks the landing page even when both are present.
        {"doi": "https://doi.org/10.1145/3448016",
         "primary_location": {"landing_page_url": "https://dl.acm.org/doi/10.1145/3448016"}},
        # No DOI — fall through to the publisher's landing page.
        {"doi": None,
         "primary_location": {"landing_page_url": "https://example.org/paper-2"}},
        # Neither — the OpenAlex id is the last absolute URL available.
        {"doi": None, "primary_location": None, "id": "https://openalex.org/W123456789"},
        # Nothing usable at all: must be dropped, not emitted as None.
        {"doi": None, "primary_location": {}},
    ],
}

ARXIV_PAYLOAD = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>https://arxiv.org/abs/2301.00001v1</id>
    <title>A Paper With Both Links</title>
    <link href="https://arxiv.org/abs/2301.00001v1" rel="alternate" type="text/html"/>
    <link href="https://arxiv.org/pdf/2301.00001v1" rel="related" type="application/pdf"/>
  </entry>
  <entry>
    <id>https://arxiv.org/abs/2301.00002v1</id>
    <title>A Paper With Only A PDF Link</title>
    <link href="https://arxiv.org/pdf/2301.00002v1" rel="related" type="application/pdf"/>
  </entry>
</feed>
"""


@pytest.fixture
def http(monkeypatch):
    """Stub the single HTTP seam and record how it was called.

    Returns the recorder so a test can assert on the outbound request — which is
    the only way to prove a date filter was actually sent rather than merely
    accepted and dropped.
    """
    def _install(payload):
        def fake(url, params):
            fake.calls.append({"url": url, "params": params})
            return payload if isinstance(payload, bytes) else json.dumps(payload).encode()

        fake.calls = []
        monkeypatch.setattr(providers, "_get_bytes", fake)
        return fake

    return _install


# --- The matrix itself ------------------------------------------------------

class TestSupportMatrix:
    """The matrix is load-bearing, so its shape is asserted rather than assumed."""

    def test_every_provider_has_a_support_entry(self):
        """A registered provider with no matrix row would silently support nothing."""
        assert set(source_ledger.REGISTRY) == set(providers.SUPPORTS)

    def test_only_ddgs_takes_an_engine_chain(self):
        """`backend` is a ddgs concept; another provider claiming it would be handed
        a comma-joined engine list it has no idea what to do with."""
        for name, supported in providers.SUPPORTS.items():
            assert ("backend" in supported) == (name == "ddgs"), name

    def test_openalex_does_not_claim_region(self):
        """Its country filter is author affiliation, not content locale. Offering it
        under the control that means "result locale" elsewhere would be a lie."""
        assert "region" not in providers.SUPPORTS["openalex"]


# --- Wikipedia --------------------------------------------------------------

class TestWikipedia:
    def test_builds_article_urls_from_titles(self, http):
        """The API returns titles, not URLs — the URL is constructed."""
        http(WIKIPEDIA_PAYLOAD)
        urls = providers.search_wikipedia("iceberg", max_results=10)
        assert urls[0] == "https://en.wikipedia.org/wiki/Apache_Iceberg"

    def test_spaces_become_underscores_not_percent_twenty(self, http):
        """"%20" would resolve but would not match the URL a browser shows, so the
        same article could survive deduplication twice."""
        http(WIKIPEDIA_PAYLOAD)
        urls = providers.search_wikipedia("iceberg", max_results=10)
        assert "%20" not in urls[0]

    def test_parentheses_and_punctuation_are_encoded(self, http):
        http(WIKIPEDIA_PAYLOAD)
        urls = providers.search_wikipedia("iceberg", max_results=10)
        assert urls[1] == "https://en.wikipedia.org/wiki/Iceberg_%28disambiguation%29"

    @pytest.mark.parametrize("region, lang", [
        ("wt-wt", "en"), ("us-en", "en"), ("uk-en", "en"),
        ("kr-kr", "ko"), ("jp-jp", "ja"), ("de-de", "de"), ("fr-fr", "fr"),
    ])
    def test_region_selects_the_language_edition(self, http, region, lang):
        """The whole reason Wikipedia is reached directly instead of through the
        ddgs backend: ko.wikipedia.org is a different corpus, not a Korean view of
        the English one."""
        recorder = http(WIKIPEDIA_PAYLOAD)
        urls = providers.search_wikipedia("iceberg", max_results=10, region=region)
        assert recorder.calls[0]["url"] == f"https://{lang}.wikipedia.org/w/api.php"
        assert urls[0].startswith(f"https://{lang}.wikipedia.org/wiki/")

    def test_unknown_region_falls_back_to_english(self, http):
        http(WIKIPEDIA_PAYLOAD)
        urls = providers.search_wikipedia("iceberg", max_results=10, region="zz-zz")
        assert urls[0].startswith("https://en.wikipedia.org/")

    def test_empty_response_returns_empty_list(self, http):
        http({"query": {"search": []}})
        assert providers.search_wikipedia("iceberg", max_results=10) == []

    def test_missing_query_key_does_not_raise(self, http):
        """MediaWiki omits `query` entirely rather than returning an empty one."""
        http({"batchcomplete": True})
        assert providers.search_wikipedia("iceberg", max_results=10) == []


# --- OpenAlex ---------------------------------------------------------------

class TestOpenAlex:
    def test_prefers_the_doi(self, http):
        """A DOI outlives a publisher's URL scheme."""
        http(OPENALEX_PAYLOAD)
        urls = providers.search_openalex("iceberg", max_results=10)
        assert urls[0] == "https://doi.org/10.1145/3448016"

    def test_falls_back_to_landing_page_then_id(self, http):
        http(OPENALEX_PAYLOAD)
        urls = providers.search_openalex("iceberg", max_results=10)
        assert urls[1] == "https://example.org/paper-2"
        assert urls[2] == "https://openalex.org/W123456789"

    def test_works_with_no_usable_url_are_dropped(self, http):
        """Not emitted as None — the contract says every element is a string."""
        http(OPENALEX_PAYLOAD)
        assert len(providers.search_openalex("iceberg", max_results=10)) == 3

    def test_timelimit_becomes_a_publication_date_filter(self, http):
        recorder = http(OPENALEX_PAYLOAD)
        providers.search_openalex("iceberg", max_results=10, timelimit="y")
        assert recorder.calls[0]["params"]["filter"].startswith("from_publication_date:")

    def test_no_timelimit_sends_no_filter(self, http):
        recorder = http(OPENALEX_PAYLOAD)
        providers.search_openalex("iceberg", max_results=10, timelimit=None)
        assert "filter" not in recorder.calls[0]["params"]

    def test_contact_is_sent_as_mailto_when_set(self, http, monkeypatch):
        monkeypatch.setenv("SOURCE_LEDGER_CONTACT", "team@example.com")
        recorder = http(OPENALEX_PAYLOAD)
        providers.search_openalex("iceberg", max_results=10)
        assert recorder.calls[0]["params"]["mailto"] == "team@example.com"

    def test_unset_contact_is_omitted_rather_than_sent_empty(self, http, monkeypatch):
        """Unset must degrade to the anonymous pool, not fail and not send ""."""
        monkeypatch.delenv("SOURCE_LEDGER_CONTACT", raising=False)
        recorder = http(OPENALEX_PAYLOAD)
        providers.search_openalex("iceberg", max_results=10)
        assert "mailto" not in recorder.calls[0]["params"]


# --- arXiv ------------------------------------------------------------------

class TestArxiv:
    def test_parses_atom_and_prefers_the_html_link(self, http):
        http(ARXIV_PAYLOAD)
        urls = providers.search_arxiv("iceberg", max_results=10)
        assert urls[0] == "https://arxiv.org/abs/2301.00001v1"

    def test_never_returns_the_pdf_link(self, http):
        """A PDF is page content. This repo returns pages to read, not files."""
        http(ARXIV_PAYLOAD)
        urls = providers.search_arxiv("iceberg", max_results=10)
        assert not any("/pdf/" in url for url in urls)

    def test_falls_back_to_the_entry_id(self, http):
        """The second entry has only a PDF link; `<id>` is the same abstract page."""
        http(ARXIV_PAYLOAD)
        urls = providers.search_arxiv("iceberg", max_results=10)
        assert urls[1] == "https://arxiv.org/abs/2301.00002v1"

    def test_timelimit_becomes_a_submitted_date_range(self, http):
        recorder = http(ARXIV_PAYLOAD)
        providers.search_arxiv("iceberg", max_results=10, timelimit="m")
        assert "submittedDate:[" in recorder.calls[0]["params"]["search_query"]

    def test_no_timelimit_sends_no_date_clause(self, http):
        recorder = http(ARXIV_PAYLOAD)
        providers.search_arxiv("iceberg", max_results=10, timelimit=None)
        assert recorder.calls[0]["params"]["search_query"] == "(all:iceberg)"

    def test_multi_word_query_is_grouped(self, http):
        """A named bug: arXiv binds `AND` to the last word only, so
        `all:apache iceberg AND submittedDate:[…]` filtered on "iceberg" alone and
        returned 2013 papers for "the past month" — unfiltered, with nothing in the
        response to say so. Parenthesising the term clause binds the date to all of
        it. Quoting would also bind it but as an exact phrase, which returns
        nothing for a natural-language query."""
        recorder = http(ARXIV_PAYLOAD)
        providers.search_arxiv("apache iceberg", max_results=10, timelimit="m")
        query = recorder.calls[0]["params"]["search_query"]

        assert query.startswith("(all:apache iceberg)")
        assert query.index("(all:") < query.index("submittedDate:")

    def test_parentheses_in_the_query_cannot_break_the_grouping(self, http):
        """The grouping above is only as good as the text dropped inside it. A
        user's own parenthesis would close the group early, putting the rest of
        their words — and the AND that follows — outside it, which is the very
        failure test_multi_word_query_is_grouped exists to prevent."""
        recorder = http(ARXIV_PAYLOAD)
        providers.search_arxiv("apache (iceberg) tables", max_results=10,
                               timelimit="m")
        query = recorder.calls[0]["params"]["search_query"]

        assert query.startswith("(all:apache iceberg tables)")
        assert query.count("(") == 1 and query.count(")") == 1
        assert query.index("(all:") < query.index("submittedDate:")

    def test_empty_feed_returns_empty_list(self, http):
        http(b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>')
        assert providers.search_arxiv("iceberg", max_results=10) == []


# --- Shared helpers ---------------------------------------------------------

class TestWindowHelper:
    """One implementation of `d`/`w`/`m`/`y`, so "the past month" cannot come to
    mean 30 days in one corpus and 31 in another."""

    @pytest.mark.parametrize("timelimit, days", [("d", 1), ("w", 7), ("m", 30), ("y", 365)])
    def test_windows_are_the_documented_lengths(self, timelimit, days):
        from datetime import datetime, timezone

        since = providers._since(timelimit)
        elapsed = datetime.now(timezone.utc) - since
        assert round(elapsed.total_seconds() / 86400) == days

    @pytest.mark.parametrize("timelimit", [None, "", "nonsense"])
    def test_no_window_means_any_time(self, timelimit):
        assert providers._since(timelimit) is None


class TestTheNetworkGuard:
    """The `no_network` fixture in conftest is a safety net, so it gets a test.

    A guard that silently stopped working would let the suite drift back to making
    real calls, and the symptom would be a slow flaky suite rather than a failure
    anyone could read.
    """

    def test_a_provider_cannot_reach_the_network_unstubbed(self):
        with pytest.raises(AssertionError, match="network"):
            providers.search_wikipedia("iceberg", max_results=1)

    def test_the_guard_covers_json_and_xml_providers_alike(self):
        """Both go through `_get_bytes`; severing it severs every provider, which
        is the property that makes one seam worth having."""
        for search in (providers.search_openalex, providers.search_arxiv):
            with pytest.raises(AssertionError, match="network"):
                search("iceberg", max_results=1)


class TestTheHttpRetry:
    """One retry, and only for the failures a second attempt can fix.

    arXiv's export API can take longer than the timeout to answer a query it has not
    served recently, which used to reach the user as a bare "The read operation timed
    out" and lose the search.
    """

    @pytest.fixture(autouse=True)
    def _no_backoff(self, monkeypatch):
        monkeypatch.setattr(providers, "HTTP_RETRY_WAIT_S", 0)

    def test_a_timeout_is_retried(self, monkeypatch):
        attempts = []

        def flaky(url, params):
            attempts.append(url)
            if len(attempts) == 1:
                raise TimeoutError("The read operation timed out")
            return b"{}"

        monkeypatch.setattr(providers, "_request_once", flaky)

        assert _REAL_GET_BYTES("https://example.org", {}) == b"{}"
        assert len(attempts) == 2

    def test_it_gives_up_at_the_attempt_ceiling(self, monkeypatch):
        """The retry must not become an unbounded loop against a dead endpoint."""
        attempts = []

        def always_times_out(url, params):
            attempts.append(url)
            raise TimeoutError("The read operation timed out")

        monkeypatch.setattr(providers, "_request_once", always_times_out)

        with pytest.raises(TimeoutError):
            _REAL_GET_BYTES("https://example.org", {})
        assert len(attempts) == providers.HTTP_ATTEMPTS

    def test_an_http_error_is_not_retried(self, monkeypatch):
        """A 404 is a real answer — asking again returns the same one, slower."""
        attempts = []

        def not_found(url, params):
            attempts.append(url)
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        monkeypatch.setattr(providers, "_request_once", not_found)

        with pytest.raises(urllib.error.HTTPError):
            _REAL_GET_BYTES("https://example.org", {})
        assert len(attempts) == 1


class TestUserAgent:
    def test_contact_is_included_when_set(self, monkeypatch):
        monkeypatch.setenv("SOURCE_LEDGER_CONTACT", "team@example.com")
        assert "team@example.com" in providers._user_agent()

    def test_identifies_the_client_even_when_unset(self, monkeypatch):
        """Wikipedia blocks generic agents, so there is always a descriptive one."""
        monkeypatch.delenv("SOURCE_LEDGER_CONTACT", raising=False)
        assert "SourceLedger" in providers._user_agent()


# --- Dispatch through the façade -------------------------------------------

class TestUnsupportedOptionsNeverReachAProvider:
    """The honesty guarantee, checked at the outbound request rather than the API.

    `text_to_urls` accepting an option and dropping it is not enough to verify —
    what matters is that no date filter appears in the request Wikipedia receives.
    """

    def test_wikipedia_ignores_a_timelimit(self, http):
        recorder = http(WIKIPEDIA_PAYLOAD)
        source_ledger.text_to_urls("iceberg", provider="wikipedia", timelimit="w")
        params = recorder.calls[0]["params"]
        assert not any("date" in key.lower() for key in params)

    def test_openalex_ignores_a_region(self, http):
        recorder = http(OPENALEX_PAYLOAD)
        source_ledger.text_to_urls("iceberg", provider="openalex", region="kr-kr")
        params = recorder.calls[0]["params"]
        assert "kr" not in json.dumps(params)

    def test_arxiv_ignores_safesearch_and_backend(self, http):
        recorder = http(ARXIV_PAYLOAD)
        source_ledger.text_to_urls("iceberg", provider="arxiv",
                           safesearch="on", backend="duckduckgo,yahoo")
        params = json.dumps(recorder.calls[0]["params"])
        assert "duckduckgo" not in params and "safe" not in params.lower()

    def test_an_unknown_provider_falls_back_to_the_default(self, monkeypatch):
        """A hand-crafted POST naming a provider that does not exist must not 500."""
        class FakeDDGS:
            def __init__(self, **kwargs):
                pass

            def text(self, query, **kwargs):
                return [{"href": "https://example.com/1"}]

        monkeypatch.setattr(source_ledger, "DDGS", FakeDDGS)
        assert source_ledger.text_to_urls("iceberg", provider="evilcorp") == [
            "https://example.com/1"]


# --- ddgs's empty result --------------------------------------------------

class TestDdgsEmptyResults:
    """ddgs signals "nothing found" by raising, so the empty case has to be told
    apart from a real failure here — otherwise an ordinary miss reaches the user as
    a red error contradicting the app's own "No results found." message."""

    @staticmethod
    def _raising(exc):
        class FakeDDGS:
            # `source_ledger` configures the client with a timeout and proxy, so a
            # stand-in has to accept construction arguments.
            def __init__(self, **kwargs):
                pass

            def text(self, query, **kwargs):
                raise exc

        return FakeDDGS

    def test_no_results_becomes_an_empty_list(self, monkeypatch):
        monkeypatch.setattr(
            source_ledger, "DDGS", self._raising(DDGSException("No results found.")))

        assert source_ledger.text_to_urls("iceberg") == []

    def test_a_real_failure_still_propagates(self, monkeypatch):
        """Rate limits and transport errors must stay visible — swallowing them
        would report a blocked engine as a query with no matches."""
        monkeypatch.setattr(
            source_ledger, "DDGS", self._raising(DDGSException("Ratelimit: HTTP 429")))

        with pytest.raises(DDGSException, match="429"):
            source_ledger.text_to_urls("iceberg")
