"""Data quality — `pipelines/jobs/url_enrichment.py` normalisation.

These run the same functions the Spark job ships to its executors as UDFs, with
no cluster involved. A URL that normalises wrong produces a duplicate row in
`curated_urls` that no downstream query can distinguish from a real one, so this
is the cheapest place to catch it.
"""

import pytest

from url_enrichment import normalize_url, url_parts


class TestNormalizeUrl:
    @pytest.mark.parametrize("raw,expected", [
        # Host case is insignificant; path case is not.
        ("https://EXAMPLE.com/Docs", "https://example.com/Docs"),
        ("HTTPS://example.com/docs", "https://example.com/docs"),
        # `www.` is cosmetic.
        ("https://www.example.com/docs", "https://example.com/docs"),
        # Trailing slash, but an empty path stays "/".
        ("https://example.com/docs/", "https://example.com/docs"),
        ("https://example.com", "https://example.com/"),
        ("https://example.com/", "https://example.com/"),
        # Fragments address a position in a page, not a page.
        ("https://example.com/docs#section-3", "https://example.com/docs"),
        # Default ports are implied; others are load-bearing.
        ("https://example.com:443/docs", "https://example.com/docs"),
        ("http://example.com:80/docs", "http://example.com/docs"),
        ("http://example.com:8080/docs", "http://example.com:8080/docs"),
    ])
    def test_cosmetic_differences_collapse(self, raw, expected):
        assert normalize_url(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("https://example.com/a?utm_source=news", "https://example.com/a"),
        ("https://example.com/a?fbclid=xyz", "https://example.com/a"),
        ("https://example.com/a?gclid=xyz&msclkid=abc", "https://example.com/a"),
        # Real parameters survive alongside stripped ones.
        ("https://example.com/a?utm_medium=email&id=42", "https://example.com/a?id=42"),
        ("https://example.com/a?q=iceberg&utm_campaign=x", "https://example.com/a?q=iceberg"),
    ])
    def test_tracking_parameters_are_stripped(self, raw, expected):
        assert normalize_url(raw) == expected

    def test_tracking_parameter_match_is_case_insensitive(self):
        assert normalize_url("https://example.com/a?UTM_SOURCE=x") == "https://example.com/a"

    def test_meaningful_query_parameters_survive(self):
        """Over-stripping is worse than under-stripping — it merges genuinely
        different pages into one curated row."""
        url = "https://example.com/search?q=iceberg&page=2&sort=date"
        assert normalize_url(url) == url

    def test_the_documented_sample_normalises_as_advertised(self):
        """One string exercising every transformation pipelines/README.md
        advertises. Nothing else asserts they all hold at once."""
        raw = "https://WWW.Example.com/Docs/?utm_source=news&topic=iceberg#intro"
        assert normalize_url(raw) == "https://example.com/Docs?topic=iceberg"

    @pytest.mark.parametrize("degenerate", [
        "", None, "not a url", "/relative/path", "javascript:alert(1)",
    ])
    def test_degenerate_input_is_returned_untouched(self, degenerate):
        """Anything that is not an absolute URL is passed through rather than
        mangled — the raw table is the audit record, so a bad value must stay
        recognisably bad instead of becoming a plausible-looking URL."""
        assert normalize_url(degenerate) == degenerate

    def test_whitespace_only_collapses_to_empty(self):
        """The one degenerate case that is not passed through verbatim: leading
        and trailing whitespace is always stripped, so a blank string is the
        result. `curated_urls` filters these out on `domain IS NOT NULL`."""
        assert normalize_url("   ") == ""

    def test_surrounding_whitespace_is_stripped(self):
        assert normalize_url("  https://example.com/docs  ") == "https://example.com/docs"

    def test_non_string_input_is_passed_through(self):
        assert normalize_url(12345) == 12345

    def test_is_idempotent(self):
        """The enrichment job MERGEs on `url`. If normalising twice gave two
        answers, a rerun would insert a second row for the same page."""
        raw = "https://WWW.Example.com/Docs/?utm_source=news&topic=iceberg#intro"
        once = normalize_url(raw)
        assert normalize_url(once) == once


class TestUrlParts:
    @pytest.mark.parametrize("url,expected", [
        ("https://docs.cloudera.com/cdp/latest", ("docs.cloudera.com", "com", "https")),
        ("http://example.co.uk/a", ("example.co.uk", "uk", "http")),
        ("https://www.example.com/a", ("example.com", "com", "https")),
        ("HTTPS://EXAMPLE.COM/a", ("example.com", "com", "https")),
    ])
    def test_splits_into_columns(self, url, expected):
        assert url_parts(url) == expected

    @pytest.mark.parametrize("degenerate", ["", None, "not a url"])
    def test_degenerate_input_yields_nulls(self, degenerate):
        """`curated_urls` filters on `domain IS NOT NULL`, so an unparseable URL
        drops out of the curated table instead of landing with a blank domain."""
        assert url_parts(degenerate) == (None, None, None)

    def test_hostless_url_has_no_domain(self):
        assert url_parts("mailto:someone@example.com")[0] is None


class TestConsistencyWithTheLocalDedupe:
    """`db.dedupe_urls()` compares raw strings; the Spark job normalises first.

    That divergence is intentional and documented — the point is that the job is
    strictly more aggressive, never less. A pair the local dedupe collapses must
    also collapse here.
    """

    @pytest.mark.parametrize("a,b", [
        ("https://example.com/docs", "https://example.com/docs"),
        ("https://example.com/docs", "https://www.example.com/docs/"),
        ("https://example.com/docs", "https://example.com/docs#top"),
        ("https://example.com/docs", "https://example.com/docs?utm_source=x"),
    ])
    def test_pairs_that_should_collapse(self, a, b):
        assert normalize_url(a) == normalize_url(b)

    @pytest.mark.parametrize("a,b", [
        ("https://example.com/docs", "https://example.com/other"),
        ("https://example.com/a?page=1", "https://example.com/a?page=2"),
        ("https://example.com/docs", "http://example.com/docs"),  # scheme matters
        ("https://a.example.com/x", "https://b.example.com/x"),
    ])
    def test_pairs_that_must_stay_distinct(self, a, b):
        assert normalize_url(a) != normalize_url(b)
