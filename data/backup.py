"""Snapshot the SQLite dev store to a local file.

The dev store is gitignored and lives at a single path, so the searches it holds
exist in exactly one place on one disk. This writes a dated second copy:

    python data/backup.py                      # -> backups/source-ledger-<utc>.db
    python data/backup.py --dir /d/archive     # somewhere else on this device
    python data/backup.py --dest my-snap.db    # an exact filename

Safe to run while `make dev` is serving — see `db.backup()` for why a file copy
is not. There is no `--execute` and no confirmation: this only ever creates a new
local file and refuses to overwrite one, so there is nothing to guard against.

Restoring is a file copy in the other direction, with the server stopped — or
point the app at the snapshot directly with `SOURCE_LEDGER_DB=<path> make dev`.
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import db  # noqa: E402

ROOT = HERE.parent
# Where a snapshot lands when no path is given. Read from the environment like
# SOURCE_LEDGER_DB, so the Store button in the app can be pointed at an external drive
# without a code change; resolved once at import, so set it before the process
# starts.
DEFAULT_DIR = Path(os.environ.get("SOURCE_LEDGER_BACKUP_DIR") or ROOT / "backups")


def default_name(now=None):
    """`source-ledger-20260811-145233.db` — UTC, so the ordering survives a DST change."""
    now = now or datetime.now(timezone.utc)
    return f"source-ledger-{now:%Y%m%d-%H%M%S}.db"


def snapshot(directory=None):
    """Back up now, letting this module choose the name. Returns the path written.

    The single entry point for an unattended backup: this CLI and the Serve
    layer's Store button both arrive here, so where snapshots live and what they
    are called is decided in one place instead of agreed between two callers.
    """
    return db.backup(Path(directory or DEFAULT_DIR) / default_name())


def counts(path):
    """Row counts read back out of the finished snapshot.

    Read from the copy rather than the source on purpose: it proves the file
    opens as a database and carries the rows, which is the only part of "the
    backup worked" that a caller actually cares about.
    """
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("searches", "search_urls")}
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR,
                        help=f"directory for the dated snapshot (default: {DEFAULT_DIR})")
    parser.add_argument("--dest", type=Path, default=None,
                        help="exact destination file; overrides --dir")
    args = parser.parse_args(argv)

    try:
        written = db.backup(args.dest) if args.dest else snapshot(args.dir)
    except FileNotFoundError:
        raise SystemExit(
            f"No SQLite store at {db.DB_PATH}.\n"
            "Run the app first (make dev) so there is something to back up.")
    except FileExistsError as exc:
        raise SystemExit(
            f"{exc} already exists. Backups are never overwritten -- "
            "delete it by hand if you mean to replace it.")

    rows = counts(written)
    # Output stays ASCII: this runs on a Windows console whose default cp1252
    # codec raises on characters like U+2192.
    print("Source Ledger backup")
    print(f"  source   {db.DB_PATH}")
    print(f"  snapshot {written.resolve()}")
    print(f"  size     {written.stat().st_size:,} bytes")
    print(f"  rows     {rows['searches']} searches, {rows['search_urls']} URLs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
