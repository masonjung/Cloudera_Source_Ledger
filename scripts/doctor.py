"""Pre-demo preflight — can this machine actually reach every corpus, right now?

Answers the question a failed demo leaves you with: was that an empty result, or a
dead network? Probes each provider, and each web engine *individually* so a single
blocked engine is visible by name instead of hidden inside the pool.

Reports conditions rather than working around them. A TLS-interception failure is
named as what it is; nothing here disables certificate verification.

Stdlib only, plus the ddgs library the web provider already needs.

    make doctor          # or: python scripts/doctor.py
"""

import os
import ssl
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "retrieval"))

import providers
import urlvestigia

# One neutral query, used everywhere. Broad enough that a healthy engine always has
# matches, so zero results means something is wrong rather than something is obscure.
PROBE = "climate change"
PROBE_RESULTS = 5

# A block is fast. A search is not. An engine that returns nothing in under this many
# seconds did not look — it refused. This is a heuristic and is reported as one: ddgs
# cannot distinguish a 200-with-a-challenge-page from a genuine miss, so the timing is
# the only signal left. Measured: refusals come back in ~0.1s, real searches in ~0.8s.
BLOCK_SECONDS = 0.3

OK, WARN, FAIL = "ok", "warn", "fail"
MARK = {OK: "PASS", WARN: "WARN", FAIL: "FAIL"}


def _describe(exc):
    """A short, human reason for a failed probe.

    Names the network conditions that masquerade as application bugs, because those
    are the ones that cost you a demo: a TLS-intercepting corporate proxy and an
    unreachable proxy both surface as opaque transport errors otherwise.
    """
    if isinstance(exc, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY" in str(exc):
        return ("TLS certificate verification failed - this network is probably "
                "intercepting TLS. Certificates are not bypassed here; use a "
                "network that does not intercept, or install the proxy's CA.")
    text = str(exc) or exc.__class__.__name__
    if "timed out" in text.lower() or isinstance(exc, TimeoutError):
        return f"timed out - {text}"
    return f"{exc.__class__.__name__}: {text}"


def _probe(label, call):
    """Run one probe and return a (label, status, detail, seconds) row."""
    start = time.monotonic()
    try:
        urls = call()
    except Exception as exc:  # noqa: BLE001 - a preflight reports, it does not raise
        return (label, FAIL, _describe(exc), time.monotonic() - start)
    seconds = time.monotonic() - start
    if urls:
        return (label, OK, f"{len(urls)} urls", seconds)
    if seconds < BLOCK_SECONDS:
        return (label, FAIL, "0 urls, too fast to be a real search - probably blocked",
                seconds)
    return (label, WARN, "0 urls - reachable, but nothing came back", seconds)


def _environment():
    """The settings that change how the probes behave, so a surprising result is
    attributable rather than mysterious."""
    contact = os.environ.get("URLVESTIGIA_CONTACT", "").strip()
    proxy = urlvestigia._ddgs_proxy()
    return [
        ("URLVESTIGIA_CONTACT", contact or "unset - using the anonymous rate-limit pool"),
        ("web proxy", proxy or "none"),
        ("web timeout", f"{urlvestigia.DDGS_TIMEOUT_S:g}s"),
        ("api timeout", f"{providers.HTTP_TIMEOUT_S:g}s x {providers.HTTP_ATTEMPTS} attempts"),
    ]


def rows():
    """Every probe, API providers first — they are the ones that should never fail.

    Public, unlike the rest of the helpers here: `quickstart.ipynb` runs the same
    preflight and then branches on the result, choosing a provider this network can
    actually reach instead of failing at the first search.
    """
    probes = []
    for name in ("wikipedia", "openalex", "arxiv"):
        probes.append(_probe(name, lambda name=name: urlvestigia.text_to_urls(
            PROBE, provider=name, max_results=PROBE_RESULTS)))
    # Each engine alone. Together they would hide exactly what this is here to find.
    for engine in ("duckduckgo", "yahoo", "startpage", "yandex"):
        probes.append(_probe(f"web: {engine}", lambda e=engine: urlvestigia.text_to_urls(
            PROBE, provider="ddgs", max_results=PROBE_RESULTS, backend=e)))
    return probes


def main():
    print(f'URLvestigia preflight - probing every corpus with "{PROBE}"\n')

    for name, value in _environment():
        print(f"  {name:22} {value}")
    print()

    probes = rows()
    for label, status, detail, seconds in probes:
        print(f"  {MARK[status]}  {label:16} {seconds:5.1f}s  {detail}")

    healthy = {label for label, status, _, _ in probes if status == OK}
    apis = {"wikipedia", "openalex", "arxiv"} & healthy
    engines = {label for label in healthy if label.startswith("web: ")}

    print()
    if not healthy:
        print("  VERDICT  Nothing is reachable. Check the network before presenting.")
        return 1
    if engines:
        names = ", ".join(sorted(e.removeprefix("web: ") for e in engines))
        print(f"  VERDICT  Demo-ready. Web works via {names}; "
              f"{len(apis)} of 3 API providers healthy.")
    else:
        print("  VERDICT  Web search is blocked from this network. Demo with "
              f"{', '.join(sorted(apis)) or 'no working provider'} instead, "
              "and expect the Web provider to fail live.")
    # A blocked engine is a measurement, not a defect — the framing `make test-live`
    # already uses. Only a total loss of connectivity is worth a non-zero exit.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
