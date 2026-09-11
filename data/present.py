"""The record, read back for a human to look at — on screen, and as a file.

`db.py` owns writing it and `record.py` owns the rules it is written under. This
owns getting it back out, and exists to keep two of those rules intact on the way —
both of which a DataFrame, and a plain HTML table, quietly break:

* **NULL is not empty.** NULL means the provider has no such option; `""` means it
  has one and this search did not use it. They render as `n/a` and `any`, because
  a table that prints both as a blank cell claims a filter that never ran. CSV has
  only one empty cell, so there the unsupported option is spelled `NULL`.
* **Read-only.** Queries go through a `file:...?mode=ro` URI, so an UPDATE typed
  into an audit query raises instead of altering the record being audited. The one
  thing here that writes, `export()`, writes a deliverable and never the store.

The export lives here rather than in `scripts/cli.py`, where it started, for the
reason `record.py` is the one writer: the flattened shape *is* a rule about the
record — one row per URL, rank as stored, NULL spelled out — and a rule two
interfaces each re-implement is a rule until one of them drifts. The terminal and
`quickstart.ipynb` now produce a byte-identical file because they run this code.
"""

import csv
import html
import sqlite3
from contextlib import closing

import db

# Deliverables, not source: `data/` is the Ingest layer's input and an export is
# its output, so it gets its own directory (gitignored — every row carries the
# query text verbatim). Pinned to the layer, not to `db.DB_PATH.parent`, because
# URLVESTIGIA_DB can point the store anywhere and the export still belongs here.
EXPORTS = db.HERE / "exports"

# One row per URL, provenance denormalized onto each. See `write_csv`.
EXPORT_COLUMNS = ("search_id", "created_at", "query", "provider", "region",
                  "safesearch", "timelimit", "backend", "max_results",
                  "position", "url")

# An export is a deliverable, not a preview, so it must not silently stop at the
# 50 rows `db.list_searches()` defaults to.
EXPORT_LIMIT = 1000

# The record as the dashboard shows it: the question, every option it was asked
# under, and how many URLs came back.
SEARCHES = """
SELECT s.query, s.id, s.created_at, s.provider, s.region, s.safesearch,
       s.timelimit, s.backend, s.max_results, COUNT(u.id) AS urls
FROM searches AS s
LEFT JOIN search_urls AS u ON u.search_id = s.id
GROUP BY s.id
ORDER BY s.id DESC
LIMIT ?
"""


def store():
    """The store, or None when nothing has been recorded yet.

    Querying a database that does not exist raises "unable to open database file",
    which is true and useless to someone three cells into a quickstart.
    """
    return db.DB_PATH if db.DB_PATH.exists() else None


def ask(sql, parameters=()):
    """Run `sql` against the store; return its column names and its rows.

    closing(), because sqlite3's own context manager ends the transaction but
    leaves the handle open — the same reason `db._db()` wraps its connections.
    """
    uri = f"{db.DB_PATH.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        cursor = conn.execute(sql, parameters)
        return [column[0] for column in cursor.description], cursor.fetchall()


def cell(value):
    """One value, with NULL and unset kept apart."""
    if value is None:
        return '<span style="opacity:.5">n/a</span>'
    if value == "":
        return '<span style="opacity:.5">any</span>'
    return html.escape(str(value))


def table(columns, rows):
    """A result set as an HTML table. Query text is user input, so it is escaped."""
    head = "".join(f'<th style="text-align:left;padding:4px 10px">{html.escape(str(c))}</th>'
                   for c in columns)
    body = "".join(
        '<tr style="border-top:1px solid rgba(128,128,128,.35)">'
        + "".join('<td style="padding:4px 10px;vertical-align:top;max-width:28em">'
                  f'{cell(value)}</td>' for value in row)
        + "</tr>" for row in rows)
    return ('<table style="border-collapse:collapse;font-size:.9em">'
            f'<tr style="border-bottom:2px solid rgba(128,128,128,.6)">{head}</tr>'
            f'{body}</table>')


def searches(limit=10):
    """The recorded searches, newest first, as a table."""
    return table(*ask(SEARCHES, (limit,)))


# --- the record as a file ----------------------------------------------------

def export_rows(limit=EXPORT_LIMIT):
    """The record flattened to one row per URL, oldest search first.

    One row per URL rather than one per search, because the deliverable is "every
    source, and how it was found": a reviewer opens it, sorts by domain, filters by
    provider. A search-grained file with a nested URL list cannot be sorted by URL
    at all, and unnesting it by hand is exactly the manual reconstruction this
    accelerator exists to remove. It is also the grain `data/iceberg/ddl.sql`
    curates into, so the CSV a reviewer reads and the table CDW holds are the same
    shape.

    `position` stays 0-based, as stored. Renumbering to 1-based here would make the
    export a second source of truth for rank.
    """
    rows = []
    for search in reversed(db.list_searches(limit=limit)):
        for position, url in enumerate(search["urls"]):
            rows.append({
                "search_id": search["id"],
                "created_at": search["created_at"],
                "query": search["query"],
                "provider": search["provider"],
                "region": search["region"],
                "safesearch": search["safesearch"],
                "timelimit": search["timelimit"],
                "backend": search["backend"],
                "max_results": search["max_results"],
                "position": position,
                "url": url,
            })
    return rows


def write_csv(rows, stream):
    """CSV, with the one distinction CSV cannot make spelled out.

    CSV has a single empty cell and this record has two meanings for it, so an
    unsupported option is written as the literal NULL and an unused one is left
    empty. JSON needs no such trick and is the faithful format; this is the
    readable one.

    lineterminator is pinned because csv defaults to \r\n and a Windows text
    stream translates the \n again, giving every row a blank line after it.
    """
    writer = csv.DictWriter(stream, fieldnames=EXPORT_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: ("NULL" if v is None else v) for k, v in row.items()})


def export(path=None, limit=EXPORT_LIMIT):
    """Write the record to `path` as CSV; return that path and the rows written.

    Defaults to `data/exports/review-appendix.csv` rather than to the working
    directory, because a notebook's cwd depends on how its kernel was started and
    an export nobody can find is an export that did not happen. Re-running
    overwrites, so the file is always the record as it stands now.
    """
    path = EXPORTS / "review-appendix.csv" if path is None else path
    path.parent.mkdir(parents=True, exist_ok=True)

    rows = export_rows(limit=limit)
    # newline="" is required by the csv module; without it Windows doubles every
    # line ending — the same reason `cli.cmd_export` opens its file that way.
    with open(path, "w", newline="", encoding="utf-8") as handle:
        write_csv(rows, handle)
    return path, rows


def head(path, lines=8, width=160):
    """The top of an export, for showing what was just written."""
    with open(path, encoding="utf-8") as handle:
        return [line[:width] for line in handle.read().splitlines()[:lines]]
