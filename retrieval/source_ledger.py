"""source_ledger — natural-language text in, a list of URLs out, via free web search.

Free to run: no API keys, no accounts. Web searches go to the engines' public pages
through the ddgs metasearch library; Wikipedia, OpenAlex, and arXiv are reached
through their own keyless APIs in providers.py. Persistence lives in data/db.py;
this module is search only.

One search uses one provider. Within `ddgs`, `backend` selects engines that are
queried *concurrently* and pooled — not tried in order until `max_results` is filled;
see "Multi-engine is resilience" in docs/ARCHITECTURE.md. Across providers there is
no chain at all, because they are different corpora: falling through from a web
engine to arXiv would answer a web-search miss with physics preprints.
"""

import contextlib
import logging
import os
import time

from ddgs import DDGS
from ddgs.exceptions import DDGSException

import providers
from providers import SUPPORTS

# ddgs search options:
# region: locale like "us-en", "kr-kr"; "wt-wt" for worldwide.
# safesearch: "off", "moderate", or "on"
# timelimit: d, w, m, y. Defaults to None (any time).
# backend: which engine(s) to query — a single name or a comma-delimited list,
#             e.g. "duckduckgo" or "duckduckgo,yahoo,startpage,yandex"; with a list,
#             ddgs stops as soon as it has max_results, falling through on failures.
#             (ddgs also supports google, brave, mojeek, wikipedia, but they are
#             blocked or empty too often from this network to expose in the UI.
#             Wikipedia is exposed as its own provider instead, via the MediaWiki
#             API, because the ddgs backend cannot map a region to a language.)
# max_results: max number of results. If None, returns results only from the first response.

DEFAULT_PROVIDER = "ddgs"

# ddgs signals "every engine answered, none had anything" by raising rather than
# returning an empty list, with this exact message. Matching on the string is
# unlovely, but it is the only signal the library exposes: the alternative is
# reporting an ordinary empty result set to the user as a hard error, in red, next
# to the app's own "No results found." saying the opposite. If a future ddgs
# reworded it, this stops matching and the behaviour reverts to what it is today.
_DDGS_EMPTY = "No results found."

# ddgs defaults to `timeout=5`, and that one number does double duty: it is the
# per-request socket timeout *and* the `wait()` budget ddgs spends collecting from
# the engines it queried concurrently (ddgs/ddgs.py:427). Engines that miss the
# window have their results dropped. Five seconds is generous on a desk and fatal
# on conference or hotel wifi, where every engine misses it and the search returns
# nothing at all — which the UI then reports as an ordinary empty result.
#
# 12 matches providers.HTTP_TIMEOUT_S so an operator has one number to reason about
# across both network paths, not two. Resolved at import, like HTTP_TIMEOUT_S, so it
# must be set before the process starts. An unparseable value falls back to the
# default rather than crashing at import: a typo in a demo machine's environment
# should not take the whole app down.
DDGS_TIMEOUT_DEFAULT_S = 12.0


def _ddgs_timeout():
    raw = os.environ.get("SOURCE_LEDGER_DDGS_TIMEOUT")
    try:
        value = float(raw) if raw else DDGS_TIMEOUT_DEFAULT_S
    except ValueError:
        return DDGS_TIMEOUT_DEFAULT_S
    return value if value > 0 else DDGS_TIMEOUT_DEFAULT_S


DDGS_TIMEOUT_S = _ddgs_timeout()

# ddgs decides how many engines to query from the size of the request:
# `max_workers = min(unique_engines, ceil(max_results / 10) + 1)` (ddgs/ddgs.py:407).
# At the UI default of `max_results=10` that is 2 — so selecting all four engines
# queries two of them and stops, and the resilience the four checkboxes advertise is
# only half delivered.
#
# Asking for enough results to cover the whole selection is what restores it. The
# caller's ceiling is re-applied centrally in `text_to_urls`, so widening the request
# here cannot widen what a caller receives.
#
# The cost is real: more engines queried means more outbound requests per search.
# That is the price of the resilience, and it is the behaviour the UI already claims.
#
# This reaches into a library internal. Re-check it on a ddgs upgrade — if the
# formula changes, this silently reverts to querying too few engines.
_DDGS_ENGINE_COUNT = 4
_DDGS_MIN_REQUEST = (_DDGS_ENGINE_COUNT - 1) * 10  # ceil(30/10) + 1 == 4 workers


def _ddgs_proxy():
    """The proxy to route web searches through, or None.

    ddgs reads only its own `DDGS_PROXY`, so on a corporate network that publishes
    the conventional `HTTPS_PROXY`/`HTTP_PROXY` it would otherwise ignore the proxy
    entirely and fail every engine. `DDGS_PROXY` still wins when both are set — it is
    the more specific instruction.

    Read on each call rather than at import so `make doctor` and the test suite can
    change it without a reload.
    """
    for name in ("DDGS_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


class EngineError(RuntimeError):
    """A web search returned nothing and at least one engine said why.

    `failures` is a list of `(engine, reason)` pairs. The Serve layer renders them,
    which is the whole point: a blocked engine and an empty corpus are the same
    screen today, and only one of them is the user's fault.
    """

    def __init__(self, failures):
        self.failures = failures
        super().__init__(", ".join(f"{engine}: {reason}" for engine, reason in failures))


class _EngineErrorCollector(logging.Handler):
    """Collects ddgs's per-engine failure records.

    ddgs logs `"Error in engine %s: %r"` at INFO (ddgs/ddgs.py:436) and then discards
    the exception unless it happens to be the last one seen. That log line is the only
    per-engine diagnostic the library exposes, so it is what we listen for.
    """

    _PREFIX = "Error in engine"

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.failures = []

    def emit(self, record):
        # Match on the format string, before interpolation, so this does not depend
        # on how a particular engine's exception happens to render.
        if str(record.msg).startswith(self._PREFIX) and len(record.args or ()) >= 2:
            engine, error = record.args[0], record.args[1]
            self.failures.append((str(engine), str(error)))


@contextlib.contextmanager
def _collect_engine_errors():
    """Capture ddgs's per-engine failures for the duration of one search.

    The logger is left exactly as it was found. Raising its level to INFO is only
    done when it would otherwise suppress the records, and propagation is disabled
    for that same window — without it, turning the level up would start emitting
    ddgs's INFO lines to the root handlers, which is console noise the operator did
    not ask for and did not have before.
    """
    logger = logging.getLogger("ddgs.ddgs")
    collector = _EngineErrorCollector()
    level, propagate = logger.level, logger.propagate
    quiet = not logger.isEnabledFor(logging.INFO)
    logger.addHandler(collector)
    if quiet:
        logger.setLevel(logging.INFO)
        logger.propagate = False
    try:
        yield collector
    finally:
        logger.removeHandler(collector)
        if quiet:
            logger.setLevel(level)
            logger.propagate = propagate


def _search_ddgs(text, *, max_results, region="wt-wt", safesearch="moderate",
                 timelimit=None, backend="duckduckgo"):
    """Web results through the ddgs metasearch library.

    A genuinely empty result comes back as `[]`. A failure — rate limited, blocked,
    transport error — propagates, because those the caller must report.

    Between those two lies the case this function exists to separate: engines that
    all failed, which ddgs reports as an empty search rather than an error whenever
    it has no exception in hand. When the engines logged failures and nothing came
    back, that is an `EngineError`, not a miss.

    One gap stays open. An engine that answers HTTP 200 with an empty body or a
    challenge page is indistinguishable from one that searched and found nothing —
    it raises nothing and logs nothing. Rate limiting frequently looks exactly like
    that, so a clean `[]` from a blocked engine is still possible here.
    """
    # Ask for enough results that every selected engine is actually queried; the
    # caller's ceiling is re-applied in `text_to_urls`. See _DDGS_MIN_REQUEST.
    requested = max(max_results, _DDGS_MIN_REQUEST)
    started = time.monotonic()
    try:
        with _collect_engine_errors() as collector:
            results = DDGS(timeout=DDGS_TIMEOUT_S, proxy=_ddgs_proxy()).text(
                text,
                region=region,
                safesearch=safesearch,
                timelimit=timelimit or None,
                backend=backend,
                max_results=requested,
            )
    except DDGSException as exc:
        if str(exc) == _DDGS_EMPTY:
            _reject_empty(collector.failures, time.monotonic() - started, exc)
            return []
        raise
    # ddgs renamed "url" to "href"; accept either so a library upgrade cannot
    # silently return zero results.
    urls = [item.get("href") or item.get("url") for item in results or []]
    if not any(urls):
        # ddgs can report a total failure as an ordinary empty list rather than raising.
        _reject_empty(collector.failures, time.monotonic() - started)
    return urls


def _reject_empty(failures, elapsed, cause=None):
    """Raise if an empty web search was a failure rather than a miss.

    Two signals, because ddgs gives two different amounts of help:

    Engines that *raised* are logged, and `failures` carries them.

    Engines that never finished are not. They are left in ddgs's `not_done` set and
    silently dropped (ddgs/ddgs.py:437) — no exception, no log, nothing. This is the
    slow-network failure exactly, and it is the one that reads as "No results found."
    on a projector. What gives it away is the clock: an empty search that consumed
    the entire collection window did not search and come up empty, it ran out of time.
    """
    if failures:
        raise EngineError(failures) from cause
    # A little under the budget, since the window closes fractionally before the
    # call returns. A search that genuinely found nothing comes back well inside it.
    if elapsed >= DDGS_TIMEOUT_S * 0.9:
        raise EngineError([(
            "all engines",
            f"no answer within {DDGS_TIMEOUT_S:g}s — the network is likely too slow "
            f"for the current SOURCE_LEDGER_DDGS_TIMEOUT",
        )]) from cause


# Provider id -> search function. ddgs is a library call and stays here; the HTTP
# providers live in providers.py.
REGISTRY = {DEFAULT_PROVIDER: _search_ddgs, **providers.REGISTRY}


def supports(provider):
    """The option names `provider` actually applies.

    Callers use this to avoid recording an option that was never applied. A
    Wikipedia search stamped `timelimit="w"` would be a false claim in a governed
    table, and the whole point of persisting the options is that a search can be
    reproduced from them.
    """
    return SUPPORTS.get(provider, frozenset())


def text_to_urls(text, *, provider=DEFAULT_PROVIDER, max_results=10, region="wt-wt",
                 safesearch="moderate", timelimit=None, backend="duckduckgo"):
    """Natural-language text -> result URLs, deduplicated, in rank order.

    Options a provider does not support are dropped rather than passed and ignored,
    so a provider can never be handed a filter it would silently discard.
    """
    text = text.strip()
    if not text:
        return []

    search = REGISTRY.get(provider) or REGISTRY[DEFAULT_PROVIDER]
    options = {
        "region": region,
        "safesearch": safesearch,
        "timelimit": timelimit or None,
        "backend": backend,
    }
    applied = {name: value for name, value in options.items()
               if name in supports(provider)}

    urls = []
    for url in search(text, max_results=max_results, **applied) or []:
        if url and url not in urls:
            urls.append(url)
    # The one place the ceiling is enforced, and the reason `_search_ddgs` is free to
    # ask ddgs for more than the caller wants: widening the request there buys engine
    # breadth without ever widening what a caller receives.
    return urls[:max_results]
