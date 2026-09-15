"""Retrieval providers — one search corpus each, behind a common interface.

`ddgs` reaches web engines by requesting their public result pages. That is free and
keyless, but every engine in the fallback chain fails the same way: rate-limited,
IP-blocked, or broken by a markup change. A chain of four does not help when the
trigger is the caller's egress IP, which is exactly the profile a datacenter address
presents.

The providers here break that correlation. Wikipedia, OpenAlex, and arXiv are
documented, versioned APIs run by non-profits whose published terms invite
programmatic use. They do not block datacenter IPs. They are also *different
corpora*, not substitute web engines — which is why a search picks one provider
rather than chaining across them. See "Providers are an axis, not links in the
chain" in docs/ARCHITECTURE.md.

Each provider returns a plain list of absolute http(s) URLs, in the order the
provider ranked them. Deduplication and the `max_results` ceiling are applied once,
in source_ledger.py, so every provider inherits the same guarantees.

Stdlib only. retrieval/requirements.txt stays at one dependency so this layer keeps the
property retrieval/README.md claims for it: importable and testable with nothing else
installed.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

# Which search options each provider actually applies. This is the single source of
# truth for the whole feature: it decides which kwargs reach a provider, which
# columns `app/server.py` writes versus leaves NULL, which controls the template
# renders, and which CSS visibility rules it emits. Declared once because a copy
# would drift silently — a UI knob that quietly does nothing is precisely the bug
# this table exists to prevent.
#
# `max_results` is absent because every provider honours it.
#
# `region` is deliberately not listed for OpenAlex. It does have a country filter,
# but it filters on *author affiliation*, not the locale of the work — offering it
# under the same control that means "result locale" elsewhere would be the exact
# dishonesty the matrix is here to remove.
SUPPORTS = {
    "ddgs": frozenset({"region", "safesearch", "timelimit", "backend"}),
    "wikipedia": frozenset({"region"}),
    "openalex": frozenset({"timelimit"}),
    "arxiv": frozenset({"timelimit"}),
}

# A short ceiling with several attempts, rather than one long wait.
#
# Measured against the live arXiv API: a normal answer arrives in well under a second,
# but the endpoint intermittently stalls for a minute or more — and it stalls by query
# *time*, not by query text, since the same search that burned 61s and failed returned
# in 0.5s a few minutes later. A single 15s attempt turned each of those blips into
# `TimeoutError: The read operation timed out` on the user's screen; a single 30s one
# only made the same failure slower. Against a stall, abandoning the socket early and
# asking again is what recovers, so the budget is spent on attempts.
#
# Worst case is roughly TIMEOUT * ATTEMPTS + WAIT * (ATTEMPTS - 1) ≈ 38s, and only on
# the path where the search has already failed. Wikipedia and OpenAlex answer in about
# a second, so the ceiling is never near them.
#
# Read from the environment so a slow or proxied network can be accommodated without a
# code change. Resolved once at import, like SOURCE_LEDGER_DB and unlike SOURCE_LEDGER_CONTACT, so it
# has to be set before the process starts.
HTTP_TIMEOUT_S = float(os.environ.get("SOURCE_LEDGER_HTTP_TIMEOUT") or 12)
HTTP_ATTEMPTS = 3
HTTP_RETRY_WAIT_S = 1.0

# Recency windows, in days. `d`/`w`/`m`/`y` are the values the UI offers; the same
# four map onto every provider that supports a period at all.
_WINDOW_DAYS = {"d": 1, "w": 7, "m": 30, "y": 365}


def _contact():
    """The operator's contact address, or "" when unset.

    Wikipedia asks for a descriptive User-Agent and OpenAlex for a `mailto`; both
    grant better rate limits to callers who identify themselves. Neither is a key
    and neither is billed. Read from the environment on each call rather than at
    import so tests and a running server can change it without a reload.
    """
    return os.environ.get("SOURCE_LEDGER_CONTACT", "").strip()


def _user_agent():
    contact = _contact()
    base = "SourceLedger/1.0 (Cloudera Forge accelerator)"
    return f"{base} ({contact})" if contact else base


def _request_once(url, params):
    """One outbound HTTP request, no retry.

    Kept separate from `_get_bytes` so the retry policy above it is testable: the
    suite severs the network by patching `_get_bytes`, so anything living inside
    that function would never run under test.
    """
    query = urllib.parse.urlencode(params, doseq=True)
    request = urllib.request.Request(
        f"{url}?{query}", headers={"User-Agent": _user_agent()})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
        return response.read()


def _get_bytes(url, params):
    """The one place this repo makes an outbound HTTP request.

    Every provider goes through here, which is what makes the seam useful: the test
    suite blocks the network by patching this single function, and any future
    retry, backoff, or proxy policy has exactly one place to live.

    Retries a timeout or a transport failure, because those are the ones another
    attempt actually fixes. An HTTPError is a real answer from the server — a 404 or
    a 403 says the same thing however often it is asked, so it is raised immediately.

    It calls search and metadata APIs only. It never dereferences a result URL —
    see "The one-line commitment" in governance/DATA_CLASSIFICATION.md.
    """
    for attempt in range(1, HTTP_ATTEMPTS + 1):
        try:
            return _request_once(url, params)
        except urllib.error.HTTPError:
            raise
        except (TimeoutError, urllib.error.URLError):
            if attempt == HTTP_ATTEMPTS:
                raise
            time.sleep(HTTP_RETRY_WAIT_S)
    # Unreachable: the loop either returns or raises on its last attempt.
    raise AssertionError("HTTP_ATTEMPTS must be at least 1")


def _get_json(url, params):
    return json.loads(_get_bytes(url, params))


def _since(timelimit):
    """`d`/`w`/`m`/`y` -> the UTC datetime that window opens, or None for any time.

    One implementation for every provider that filters by date, so "the past month"
    cannot come to mean 30 days in one corpus and 31 in another.
    """
    days = _WINDOW_DAYS.get(timelimit or "")
    return datetime.now(timezone.utc) - timedelta(days=days) if days else None


# --- Wikipedia --------------------------------------------------------------

# `region` selects the language edition, which is the honest reading of "area" for
# an encyclopedia: ko.wikipedia.org is not a Korean view of the English corpus, it
# is a different corpus. Regions with no distinct edition fall back to English.
WIKIPEDIA_LANGS = {"kr-kr": "ko", "jp-jp": "ja", "de-de": "de", "fr-fr": "fr"}
WIKIPEDIA_DEFAULT_LANG = "en"


def _wikipedia_lang(region):
    return WIKIPEDIA_LANGS.get(region or "", WIKIPEDIA_DEFAULT_LANG)


def search_wikipedia(text, *, max_results, region="wt-wt", **_ignored):
    """Article URLs from the MediaWiki search API.

    Reached directly rather than through the `ddgs` wikipedia backend, which cannot
    map a region onto the language edition — the one thing that makes `region`
    meaningful here.
    """
    lang = _wikipedia_lang(region)
    payload = _get_json(f"https://{lang}.wikipedia.org/w/api.php", {
        "action": "query",
        "list": "search",
        "srsearch": text,
        "srlimit": max_results,
        "format": "json",
        "formatversion": 2,
    })
    hits = (payload.get("query") or {}).get("search") or []
    urls = []
    for hit in hits:
        # The API returns titles, not URLs; the canonical article URL is built from
        # the title. Underscores before percent-encoding, or spaces come back as
        # "%20" and the URL stops matching the one a browser shows.
        title = hit.get("title")
        if title:
            slug = urllib.parse.quote(title.replace(" ", "_"))
            urls.append(f"https://{lang}.wikipedia.org/wiki/{slug}")
    return urls


# --- OpenAlex ---------------------------------------------------------------

def search_openalex(text, *, max_results, timelimit=None, **_ignored):
    """Scholarly work URLs from OpenAlex, newest-eligible first by relevance."""
    params = {"search": text, "per-page": max_results}
    since = _since(timelimit)
    if since:
        params["filter"] = f"from_publication_date:{since:%Y-%m-%d}"
    contact = _contact()
    if contact:
        params["mailto"] = contact  # the polite pool; omitted when unset

    payload = _get_json("https://api.openalex.org/works", params)
    urls = []
    for work in payload.get("results") or []:
        # Three shapes for the same idea, in descending order of durability: a DOI
        # outlives a publisher's URL scheme, and the OpenAlex id outlives both.
        # All three are returned as absolute URLs, which the retrieval contract
        # requires — a bare "10.1234/x" would fail it.
        location = work.get("primary_location") or {}
        url = work.get("doi") or location.get("landing_page_url") or work.get("id")
        if url:
            urls.append(url)
    return urls


# --- arXiv ------------------------------------------------------------------

_ATOM = "{http://www.w3.org/2005/Atom}"


def search_arxiv(text, *, max_results, timelimit=None, **_ignored):
    """Preprint abstract-page URLs from the arXiv Atom API.

    The only provider that answers in XML rather than JSON, parsed with stdlib
    ElementTree. That is a deliberate shape test for the seam: a provider layer
    that only ever handled JSON would have quietly grown a JSON-shaped assumption.
    """
    # The term clause is parenthesised so a date range ANDs against all of it.
    # Without the parentheses arXiv binds AND to the last word alone, and a
    # multi-word query comes back silently unfiltered — "the past month" returning
    # 2013 papers, with nothing in the response to say the filter was dropped.
    # Verified against the live API; see test_multi_word_query_is_grouped.
    #
    # A parenthesis in the user's own text would close that group early and leave
    # the rest of their words outside it — reintroducing the exact bug the
    # grouping exists to prevent, for any query containing one. `all:` matches
    # terms rather than sub-expressions, so dropping them removes nothing a
    # search could have used.
    terms = " ".join(text.replace("(", " ").replace(")", " ").split())
    query = f"(all:{terms})"
    since = _since(timelimit)
    if since:
        now = datetime.now(timezone.utc)
        query += f" AND submittedDate:[{since:%Y%m%d%H%M} TO {now:%Y%m%d%H%M}]"

    raw = _get_bytes("https://export.arxiv.org/api/query", {
        "search_query": query,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    })
    feed = ET.fromstring(raw)
    urls = []
    for entry in feed.findall(f"{_ATOM}entry"):
        # Prefer the explicit HTML link; fall back to <id>, which is the same abs
        # page URL. Never the PDF link — a PDF is page content, and this repo
        # returns pages to read, not files to fetch.
        url = next((link.get("href") for link in entry.findall(f"{_ATOM}link")
                    if link.get("rel") == "alternate"
                    and link.get("type") == "text/html"), None)
        url = url or (entry.findtext(f"{_ATOM}id") or "").strip()
        if url:
            urls.append(url)
    return urls


# Provider id -> search function. `ddgs` is absent on purpose: it is a library call
# rather than an HTTP one and lives in source_ledger.py, which assembles the full registry.
REGISTRY = {
    "wikipedia": search_wikipedia,
    "openalex": search_openalex,
    "arxiv": search_arxiv,
}
