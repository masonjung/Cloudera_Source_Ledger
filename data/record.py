"""One search becomes one record — the single writer of a governed search row.

Three interfaces now produce search records: the dashboard (`app/server.py`), the
terminal (`scripts/cli.py`), and `quickstart.ipynb`. They must agree on one thing
above all, the rule `db.save_search()` documents: an option a provider does not
apply is stored NULL, never as the value the caller happened to pass. A Wikipedia
search stamped `timelimit="w"` claims a filter that never ran, and a record that
overstates itself is worth less than no record at all.

That rule used to be enforced in the Serve layer, where only the Serve layer could
reach it. It lives here instead so the three callers share one implementation
rather than three copies that agree until one of them drifts.

**This module reaches up to retrieval/, which nothing else in data/ does.** The
inversion is deliberate and it is the point: the NULL rule is unenforceable
without `source_ledger.supports()`, so the layer that owns the contract has to be
able to ask which options a provider actually applied. It imports the support
matrix and the search entry point, and nothing else.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))                       # db
sys.path.insert(0, str(HERE.parent / "retrieval"))  # source_ledger

import db  # noqa: E402
import source_ledger  # noqa: E402

# Allowed values per search option; first entry is the default fallback.
#
# `provider` currently offers every corpus retrieval/ implements, but it stays a
# whitelist rather than a mirror of source_ledger.REGISTRY — the two are allowed to
# diverge. Anything not listed here is coerced back to the default by pick(), so
# a provider withheld from the UI cannot be reached by posting it by hand either.
# (The CLI declines rather than coerces — see scripts/cli.py for why a typed
# command and an untrusted form post deserve different answers.)
OPTIONS = {
    "provider": ["ddgs", "wikipedia", "openalex", "arxiv"],
    "timelimit": ["", "d", "w", "m", "y"],
    "backend": ["duckduckgo", "yahoo", "startpage", "yandex"],
    "safesearch": ["moderate", "off", "on"],
    "region": ["wt-wt", "us-en", "uk-en", "kr-kr", "jp-jp", "de-de", "fr-fr"],
}

# Labels for every provider that has ever been searched, not just the ones on offer:
# the dashboard's Provider column reads this, so dropping a retired provider's
# label here would relabel its historical rows with the bare id.
PROVIDER_LABELS = {
    "ddgs": "Web",
    "wikipedia": "Wikipedia",
    "openalex": "OpenAlex",
    "arxiv": "arXiv",
}

# The per-search options a provider might not apply. `backend` is in here with the
# rest: the engine chain is just another control that only one provider supports.
TOGGLEABLE = ("region", "safesearch", "timelimit", "backend")

DEFAULT_MAX_RESULTS = 10
# A ceiling, not a preference. Every provider is a public endpoint reached without
# an API key, and the polite thing to do with an anonymous quota is not to ask for
# ten thousand results because a caller typed it.
MAX_RESULTS = 50


def pick(name, value):
    """Coerce `value` to a whitelisted one, falling back to the default."""
    allowed = OPTIONS[name]
    return value if value in allowed else allowed[0]


def label(provider):
    """The human name for a provider id, or the bare id for one never named."""
    return PROVIDER_LABELS.get(provider, provider)


def normalize(*, provider=None, max_results=None, timelimit=None,
              safesearch=None, region=None, backend=None):
    """Whatever a caller supplied -> the exact option set the search will use.

    Every interface hands this partial or untrusted input: a form post, an argparse
    namespace, a notebook cell with two kwargs set. The result is the same dict in
    all three cases, and it is both what gets searched with and what gets recorded,
    so those two can never disagree.

    `backend` accepts a list (checked boxes, repeated --backend flags) or a
    comma-delimited string (the form it is stored in), and returns the stored form.
    """
    # Selected engines, whitelist-filtered and deduped. None selected means all of
    # them, not one of them — for resilience, not coverage. Selecting more engines
    # is *not* free extra results: ddgs queries them concurrently and drops the ones
    # that miss its first wait(), so a wider selection can return fewer URLs than
    # the best single engine (see "Multi-engine is resilience" in
    # docs/ARCHITECTURE.md). What it buys is that one blocked engine no longer
    # empties the search, and that is the failure actually being seen — duckduckgo
    # alone returned nothing on every attempt from this network while all four
    # returned results on every attempt.
    if isinstance(backend, str):
        backend = backend.split(",")
    engines = [b for b in dict.fromkeys(backend or []) if b in OPTIONS["backend"]]

    if max_results is None:
        max_results = DEFAULT_MAX_RESULTS
    return {
        "provider": pick("provider", provider),
        "max_results": min(max(int(max_results), 1), MAX_RESULTS),
        "region": pick("region", region),
        "safesearch": pick("safesearch", safesearch),
        "timelimit": pick("timelimit", timelimit),
        "backend": ",".join(engines or OPTIONS["backend"]),
    }


def search(text, options):
    """Run one search. Raises whatever the provider raised — callers report.

    Deliberately does not catch. The dashboard turns a failure into a flash message
    and the CLI into a stderr line and an exit code; both want the exception, not a
    sentinel that loses which engine said what.
    """
    return source_ledger.text_to_urls(
        text,
        provider=options["provider"],
        max_results=options["max_results"],
        region=options["region"],
        safesearch=options["safesearch"],
        # "" is the *record* value for "supported, left unset"; a provider has to be
        # handed None. Collapsing the two here would pass an empty string to ddgs as
        # though it were a time window.
        timelimit=options["timelimit"] or None,
        backend=options["backend"],
    )


def save(text, urls, options):
    """Persist the search and its URLs. Returns the new search id.

    The one place the NULL rule is applied. Driven off `TOGGLEABLE` and the live
    support matrix rather than a hand-written list, so a fifth provider — or a
    fifth option — is covered by being added in one place.
    """
    supported = source_ledger.supports(options["provider"])
    return db.save_search(
        text, urls,
        provider=options["provider"],
        max_results=options["max_results"],
        **{name: (options[name] if name in supported else None)
           for name in TOGGLEABLE},
    )


def run(text, *, store=True, **options):
    """Normalize, search, and record — the whole path, for callers that want it.

    Returns {"text", "options", "urls", "search_id"}; `search_id` is None when
    nothing was stored, either because `store=False` or because the search found
    nothing. An empty search is never recorded: a row with no URLs is not a search
    that happened, it is a search that failed to say so.

    The dashboard does not use this — it interleaves its own error handling and
    flash messages between the steps. The CLI and the notebook do.
    """
    text = text.strip()
    opts = normalize(**options)
    urls = search(text, opts) if text else []
    search_id = save(text, urls, opts) if urls and store else None
    return {"text": text, "options": opts, "urls": urls, "search_id": search_id}
