"""URLvestigia from the terminal — search, record, and read back the record.

The dashboard is the interface for people who do not write code. This is the one
for people who live in a shell: it writes the same rows, through the same writer
(`data/record.py`), so a search recorded here is indistinguishable from one
recorded by the app.

    python scripts/cli.py search "GLP-1 receptor agonist adverse events"
    python scripts/cli.py search "iceberg compaction" --provider arxiv -n 25
    python scripts/cli.py list --limit 5 --urls
    python scripts/cli.py export --format csv --out review-appendix.csv
    python scripts/cli.py doctor

**URLs go to stdout; everything else goes to stderr.** So `search ... > urls.txt`
leaves a file of URLs and nothing else, and the provenance you need in order to
trust them is still on your screen. That split is the whole reason this exists as
well as the dashboard.

Exit codes, because a script has to be able to tell these apart:

    0  results found (and recorded, unless --no-store)
    1  the search failed - an engine errored, or the network is down
    2  usage error
    3  zero results - the corpus answered, and had nothing

Collapsing 1 and 3 would repeat in the terminal the exact mistake the dashboard's
EngineError branch exists to prevent: a dead network wearing the face of an empty
corpus. Output is ASCII only, because a Windows console is cp1252 by default and a
tool that crashes on its own success message is not a tool.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "retrieval"))
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "scripts"))  # so `import doctor` resolves when this
                                           # file is loaded by path, as tests do

import backup  # noqa: E402
import db  # noqa: E402
import doctor  # noqa: E402
import present  # noqa: E402
import record  # noqa: E402
import urlvestigia  # noqa: E402

# The `timelimit` whitelist stores "supported, but not used" as "", which argparse
# cannot offer as a choice. `any` is not a new vocabulary word: it is what the
# dashboard already renders that column as.
ANY = "any"

# The export shape is a rule about the record, so it is owned in `data/`, beside
# the writer whose columns it flattens — not re-implemented once per interface.
# Re-exported under the names this module has always used, because they are what
# `--format csv` is documented as producing.
EXPORT_COLUMNS = present.EXPORT_COLUMNS
EXPORT_LIMIT = present.EXPORT_LIMIT
export_rows = present.export_rows
write_csv = present.write_csv


def err(message=""):
    """Everything that is not a result URL."""
    print(message, file=sys.stderr)


# --- search ----------------------------------------------------------------

def _report(text, opts, urls, search_id):
    """What was asked, what was applied, and what was not — on stderr.

    The `not applied` line is the product's central claim rendered for a terminal:
    it names the options this corpus ignored, so nobody has to infer from a stored
    NULL later that the filter they set never ran.
    """
    supported = urlvestigia.supports(opts["provider"])
    err("URLvestigia search")
    err(f"  query        {text}")
    err(f"  provider     {record.label(opts['provider'])}")

    applied = [f"{name}={opts[name] or ANY}"
               for name in record.TOGGLEABLE if name in supported]
    err(f"  applied      max_results={opts['max_results']}"
        + ("  " + "  ".join(applied) if applied else ""))
    ignored = [name for name in record.TOGGLEABLE if name not in supported]
    if ignored:
        err(f"  not applied  {', '.join(ignored)}"
            f"  ({record.label(opts['provider'])} has no such option; recorded NULL)")

    if search_id is None:
        err(f"  found        {len(urls)} urls (not recorded)")
    else:
        err(f"  saved        search #{search_id}, {len(urls)} urls, in {db.DB_PATH}")


def cmd_search(args):
    text = " ".join(args.query).strip()
    if not text:
        err("error: nothing to search for")
        return 2

    opts = record.normalize(
        provider=args.provider, max_results=args.max_results,
        timelimit="" if args.timelimit == ANY else args.timelimit,
        safesearch=args.safesearch, region=args.region, backend=args.backend,
    )
    try:
        # normalize/search/save rather than record.run(), for the same reason the
        # dashboard does it the long way: both failure branches below need the
        # normalized options in order to name what was asked.
        urls = record.search(text, opts)
    except urlvestigia.EngineError as exc:
        # Every engine failed and each said why. Named individually, because "the
        # search failed" and "yandex is blocked from this office" are different
        # problems with different fixes.
        err(f"error: {record.label(opts['provider'])} search failed"
            f" - no engine answered")
        for engine, reason in exc.failures:
            err(f"  {engine}: {reason}")
        err("Run 'python scripts/cli.py doctor' to see what this network reaches.")
        return 1
    except Exception as exc:  # noqa: BLE001 - a CLI reports; it does not traceback
        where = record.label(opts["provider"])
        if opts["provider"] == "ddgs":
            where += f" ({opts['backend'].replace(',', ' + ')})"
        err(f"error: {where} search failed - {exc}")
        return 1

    # Nothing empty is ever recorded: a row with no URLs is not a search that
    # happened, it is a search that failed to say so.
    search_id = record.save(text, urls, opts) if urls and not args.no_store else None

    if args.json:
        json.dump({"query": text, "options": opts, "urls": urls,
                   "search_id": search_id}, sys.stdout, indent=2)
        print()
    else:
        for url in urls:
            print(url)

    if not urls:
        err(f'No results. The corpus answered and had nothing for "{text}".')
        return 3
    if not args.quiet:
        _report(text, opts, urls, search_id)
    return 0


# --- reading the record ----------------------------------------------------

def _shown(value):
    """A stored option, as a person reads it. NULL and "" do not look alike."""
    if value is None:
        return "n/a"
    return value or ANY


def cmd_list(args):
    rows = db.list_searches(limit=args.limit)
    if args.json:
        json.dump(rows, sys.stdout, indent=2)
        print()
        return 0
    if not rows:
        err(f"No searches recorded yet in {db.DB_PATH}.")
        return 0

    for row in rows:
        print(f"#{row['id']:<4} {row['created_at'][:19]}  "
              f"{record.label(row['provider']):<10} {len(row['urls']):>3} urls  "
              f"{row['query']}")
        print(f"      region={_shown(row['region'])} "
              f"safesearch={_shown(row['safesearch'])} "
              f"timelimit={_shown(row['timelimit'])} "
              f"backend={_shown(row['backend'])}")
        if args.urls:
            for url in row["urls"]:
                print(f"      {url}")
    err(f"\n{len(rows)} searches from {db.DB_PATH}. "
        f"n/a = this corpus has no such option.")
    return 0


def cmd_stats(args):
    counts = db.stats()
    print(f"searches  {counts['searches']}")
    print(f"urls      {counts['urls']}")
    print(f"store     {db.DB_PATH}")
    return 0


# --- export ----------------------------------------------------------------

def write_json(rows, stream):
    json.dump(rows, stream, indent=2)
    stream.write("\n")


def cmd_export(args):
    rows = export_rows(limit=args.limit)
    write = write_csv if args.format == "csv" else write_json

    if args.out:
        # newline="" is required by the csv module; without it Windows doubles
        # every line ending.
        with open(args.out, "w", newline="", encoding="utf-8") as handle:
            write(rows, handle)
        err(f"Wrote {len(rows)} urls to {args.out}")
    else:
        write(rows, sys.stdout)
        err(f"\n{len(rows)} urls from the {args.limit} most recent searches.")
    if args.format == "csv":
        err("NULL = this corpus has no such option; "
            "empty = it has one, and this search did not set it.")
    return 0


# --- delegated ---------------------------------------------------------------

def cmd_backup(args):
    argv = []
    if args.dir:
        argv += ["--dir", args.dir]
    if args.dest:
        argv += ["--dest", args.dest]
    return backup.main(argv) or 0


def cmd_doctor(args):
    # Returns 0 even when the web is blocked, deliberately - see doctor.main().
    # A wrapper that second-guessed that would turn a measurement into a failure.
    return doctor.main()


# --- argument parsing ------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="python scripts/cli.py",
        description="Search four corpora and keep a governed record of how.",
        epilog="Exit codes: 0 results, 1 search failed, 2 usage, 3 no results.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # Every `choices` reads the whitelist rather than copying it, so an option
    # added to data/record.py is offered here without a second edit.
    search = sub.add_parser("search", help="run a search and record it")
    search.add_argument("query", nargs="+", help="what to search for")
    search.add_argument("--provider", default="ddgs",
                        choices=record.OPTIONS["provider"],
                        help="which corpus to search (default: ddgs, the open web)")
    search.add_argument("-n", "--max-results", type=int,
                        default=record.DEFAULT_MAX_RESULTS,
                        help=f"how many urls to keep (1-{record.MAX_RESULTS})")
    search.add_argument("--region", default="wt-wt", choices=record.OPTIONS["region"])
    search.add_argument("--safesearch", default="moderate",
                        choices=record.OPTIONS["safesearch"])
    search.add_argument("--timelimit", default=ANY,
                        choices=[ANY] + [t for t in record.OPTIONS["timelimit"] if t],
                        help="only results from the last day/week/month/year")
    search.add_argument("--backend", action="append",
                        choices=record.OPTIONS["backend"],
                        help="web engine to ask; repeatable. Omitted means all of "
                             "them - resilience against one being blocked, not "
                             "wider coverage. ddgs only.")
    search.add_argument("--no-store", action="store_true",
                        help="search without writing a record")
    search.add_argument("--json", action="store_true",
                        help="emit the query, options, and urls as json")
    search.add_argument("--quiet", action="store_true",
                        help="urls only; no provenance on stderr")
    search.set_defaults(func=cmd_search)

    listing = sub.add_parser("list", help="show recorded searches, newest first")
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--urls", action="store_true", help="show each url too")
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(func=cmd_list)

    stats = sub.add_parser("stats", help="how much is in the store")
    stats.set_defaults(func=cmd_stats)

    export = sub.add_parser("export", help="the record as one row per url")
    export.add_argument("--format", default="csv", choices=["csv", "json"])
    export.add_argument("--limit", type=int, default=EXPORT_LIMIT,
                        help="how many searches to include (default: %(default)s)")
    export.add_argument("--out", help="write to this file instead of stdout")
    export.set_defaults(func=cmd_export)

    snapshot = sub.add_parser("backup", help="dated snapshot of the store")
    snapshot.add_argument("--dir", help="directory to write into")
    snapshot.add_argument("--dest", help="exact filename to write")
    snapshot.set_defaults(func=cmd_backup)

    check = sub.add_parser("doctor", help="probe every corpus and engine")
    check.set_defaults(func=cmd_doctor)

    return parser


def main(argv=None):
    """A misspelled provider is a usage error here, not a coercion.

    The dashboard coerces an unknown provider back to the default because a form
    post is untrusted input arriving over the wire, and the alternative is a 500.
    A typed command is a human being making a typo, and silently searching the web
    when they asked for arXiv is worse than a usage message.
    """
    args = build_parser().parse_args(argv)
    # The store has to exist before the first command that writes to it, exactly
    # as `app/server.py` ensures at import. Idempotent, and it also migrates a
    # database created before the settings columns existed — so a store the
    # dashboard has been writing to for months is readable here without a step.
    db.init_db()
    return args.func(args)


if __name__ == "__main__":
    # Everything this file prints is ASCII, but not everything it prints *out* is:
    # a result URL can carry an internationalised domain or an undecoded path, and
    # a provider's error text is whatever that library wrote. On a Windows console
    # — cp1252 by default — encoding one of those raises UnicodeEncodeError, which
    # would kill the command halfway through listing URLs it had already recorded.
    # Degrade the unprintable character instead of losing the output.
    #
    # Done here rather than in main() so that importing this module, or calling
    # main() from a test, leaves the caller's streams exactly as it found them.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="backslashreplace")
    raise SystemExit(main())
