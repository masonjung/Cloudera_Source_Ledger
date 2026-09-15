"""Lakehouse layer — the local snapshot in `db.backup()` and `data/backup.py`.

A backup is only worth having if it is a *readable database with the rows in it*,
so these assert on reading the snapshot back rather than on the file existing.
The refuse-to-overwrite and cleanup-on-failure paths are covered too: both exist
to stop a bad run from destroying the good copy it was meant to protect.
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# data/ is on sys.path via conftest, but backup.py is a script rather than part
# of the `db` module, so load it by file path the way test_ingest.py does.
_spec = importlib.util.spec_from_file_location("backup", ROOT / "data" / "backup.py")
backup_cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backup_cli)


@pytest.fixture
def populated_db(temp_db):
    temp_db.save_search(
        "iceberg", ["https://ko.wikipedia.org/wiki/X"],
        provider="wikipedia", region="kr-kr",
        safesearch=None, timelimit=None, backend=None, max_results=10,
    )
    temp_db.save_search(
        "cdp use cases", ["https://iceberg.apache.org/", "https://example.com/1"],
        provider="ddgs", region="wt-wt",
        safesearch="moderate", timelimit="", backend="duckduckgo,yahoo",
        max_results=10,
    )
    return temp_db


# --- db.backup() -----------------------------------------------------------

class TestBackup:
    def test_snapshot_holds_the_rows(self, populated_db, tmp_path):
        populated_db.backup(tmp_path / "snap.db")

        conn = sqlite3.connect(tmp_path / "snap.db")
        try:
            assert conn.execute("SELECT COUNT(*) FROM searches").fetchone()[0] == 2
            assert conn.execute("SELECT COUNT(*) FROM search_urls").fetchone()[0] == 3
        finally:
            conn.close()

    def test_snapshot_preserves_null_versus_empty_options(self, populated_db,
                                                          tmp_path, monkeypatch):
        """The distinction the whole schema is built around has to survive a
        snapshot, or a restored database quietly claims filters that never ran."""
        populated_db.backup(tmp_path / "snap.db")

        monkeypatch.setattr(populated_db, "DB_PATH", tmp_path / "snap.db")
        wiki, web = populated_db.list_searches()[1], populated_db.list_searches()[0]

        assert wiki["timelimit"] is None
        assert web["timelimit"] == ""

    def test_source_is_left_usable(self, populated_db, tmp_path):
        """The app keeps serving during a backup, so the source must survive it."""
        populated_db.backup(tmp_path / "snap.db")

        populated_db.save_search("after", ["https://example.com/2"])
        assert populated_db.stats() == {"searches": 3, "urls": 4}

    def test_creates_missing_directories(self, populated_db, tmp_path):
        written = populated_db.backup(tmp_path / "a" / "b" / "snap.db")

        assert written.exists()

    def test_refuses_to_overwrite(self, populated_db, tmp_path):
        dest = tmp_path / "snap.db"
        populated_db.backup(dest)

        with pytest.raises(FileExistsError):
            populated_db.backup(dest)

    def test_overwrite_refusal_leaves_the_first_snapshot_intact(self, populated_db,
                                                               tmp_path):
        dest = tmp_path / "snap.db"
        populated_db.backup(dest)
        before = dest.read_bytes()

        with pytest.raises(FileExistsError):
            populated_db.backup(dest)

        assert dest.read_bytes() == before

    def test_missing_source_is_an_error_not_an_empty_snapshot(self, temp_db,
                                                              tmp_path, monkeypatch):
        """sqlite3.connect() creates what it cannot open, so without the guard a
        backup of a nonexistent store would report success over zero rows."""
        monkeypatch.setattr(temp_db, "DB_PATH", tmp_path / "gone.db")

        with pytest.raises(FileNotFoundError):
            temp_db.backup(tmp_path / "snap.db")
        assert not (tmp_path / "snap.db").exists()

    def test_a_failed_backup_leaves_no_file_behind(self, populated_db, tmp_path,
                                                   monkeypatch):
        """A half-written file that looks like a backup is worse than none: the
        next run would refuse to overwrite it and the real snapshot never lands.

        `sqlite3.connect()` creates the destination before a single page is
        copied, so the cleanup is load-bearing rather than defensive — the
        assertion on `dest_existed` is what keeps this test honest about that.
        """
        dest = tmp_path / "snap.db"
        seen = {}

        class ExplodingSource:
            """Stands in for the source connection; `sqlite3.Connection` is a C
            type and its methods cannot be patched.

            `db._db()` is a context manager, so the stub is one too — it is
            substituted for that function, not for the connection it yields.
            """

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def backup(self, target, **kwargs):
                seen["dest_existed"] = dest.exists()
                raise sqlite3.OperationalError("disk I/O error")

            def close(self):
                pass

        monkeypatch.setattr(populated_db, "_db", ExplodingSource)

        with pytest.raises(sqlite3.OperationalError):
            populated_db.backup(dest)

        assert seen["dest_existed"]  # there was something to clean up
        assert not dest.exists()


# --- the CLI ---------------------------------------------------------------

class TestCli:
    def test_writes_a_dated_snapshot_into_the_directory(self, populated_db,
                                                        tmp_path, capsys):
        backup_cli.main(["--dir", str(tmp_path)])

        written = list(tmp_path.glob("source-ledger-*.db"))
        assert len(written) == 1
        assert "2 searches, 3 URLs" in capsys.readouterr().out

    def test_dest_overrides_dir(self, populated_db, tmp_path, capsys):
        backup_cli.main(["--dest", str(tmp_path / "exact.db")])

        assert (tmp_path / "exact.db").exists()

    def test_reports_counts_read_from_the_snapshot(self, populated_db, tmp_path):
        backup_cli.main(["--dest", str(tmp_path / "snap.db")])

        assert backup_cli.counts(tmp_path / "snap.db") == {
            "searches": 2, "search_urls": 3}

    def test_existing_file_exits_without_overwriting(self, populated_db, tmp_path):
        dest = tmp_path / "snap.db"
        dest.write_bytes(b"not a database")

        with pytest.raises(SystemExit):
            backup_cli.main(["--dest", str(dest)])
        assert dest.read_bytes() == b"not a database"

    def test_missing_store_exits_with_a_pointer_to_make_dev(self, temp_db,
                                                            tmp_path, monkeypatch):
        monkeypatch.setattr(backup_cli.db, "DB_PATH", tmp_path / "gone.db")

        with pytest.raises(SystemExit) as exc:
            backup_cli.main(["--dest", str(tmp_path / "snap.db")])
        assert "make dev" in str(exc.value)

    def test_snapshot_names_itself_and_lands_in_the_given_directory(self,
                                                                    populated_db,
                                                                    tmp_path):
        """The entry point the Store button uses; the CLI reaches the same one, so
        a snapshot taken from the app and one taken from the shell are the same
        artifact in the same place."""
        written = backup_cli.snapshot(tmp_path)

        assert written.parent == tmp_path
        assert written.name.startswith("source-ledger-")
        assert backup_cli.counts(written) == {"searches": 2, "search_urls": 3}

    def test_snapshot_defaults_to_the_module_directory(self, populated_db,
                                                       tmp_path, monkeypatch):
        """`DEFAULT_DIR` is read at call time, which is what lets `SOURCE_LEDGER_BACKUP_DIR`
        and the test suite redirect it without touching the caller."""
        monkeypatch.setattr(backup_cli, "DEFAULT_DIR", tmp_path / "elsewhere")
        written = backup_cli.snapshot()

        assert written.parent == tmp_path / "elsewhere"

    def test_default_name_sorts_chronologically(self):
        """UTC and zero-padded, so `ls` orders the snapshots by age even across a
        DST change — the one thing the filename has to get right."""
        from datetime import datetime, timezone

        earlier = backup_cli.default_name(datetime(2026, 8, 11, 9, 5, 3, tzinfo=timezone.utc))
        later = backup_cli.default_name(datetime(2026, 8, 11, 14, 52, 33, tzinfo=timezone.utc))

        assert earlier == "source-ledger-20260811-090503.db"
        assert sorted([later, earlier]) == [earlier, later]
