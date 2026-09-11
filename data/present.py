"""The record, read back for a human to look at.

`db.py` owns writing it and `record.py` owns the rules it is written under. This
owns showing it, and exists to keep two of those rules intact on the way out —
both of which a DataFrame, and a plain HTML table, quietly break:

* **NULL is not empty.** NULL means the provider has no such option; `""` means it
  has one and this search did not use it. They render as `n/a` and `any`, because
  a table that prints both as a blank cell claims a filter that never ran.
* **Read-only.** Queries go through a `file:...?mode=ro` URI, so an UPDATE typed
  into an audit query raises instead of altering the record being audited.
"""

import html
import sqlite3
from contextlib import closing

import db

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
