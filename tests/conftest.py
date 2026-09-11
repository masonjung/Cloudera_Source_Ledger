"""Shared fixtures and import-path setup for the URLvestigia test suite.

The accelerator's layers are directories, not installed packages, so tests add
them to `sys.path` the same way `app/server.py` does at runtime. Import order
matters here: `URLVESTIGIA_DB` must be redirected **before** anything imports `db`,
because `db.DB_PATH` is resolved once at module import.
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Redirect the SQLite store to a throwaway file before `db` is ever imported.
# Without this, running the suite would write to the developer's real database.
_TMP_DB = Path(tempfile.gettempdir()) / "urlvestigia-pytest.db"
os.environ["URLVESTIGIA_DB"] = str(_TMP_DB)

for layer in ("retrieval", "data", "pipelines/jobs", "scripts"):
    sys.path.insert(0, str(ROOT / layer))
sys.path.insert(0, str(ROOT))  # so `import app.server` resolves

import pytest  # noqa: E402


def pytest_addoption(parser):
    parser.addoption(
        "--live", action="store_true", default=False,
        help="Run tests that call real search engines (tests/eval/). Off by "
             "default so CI never fails because a provider is rate-limiting.",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live: hits real search engines; requires --live")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="needs --live (calls real search engines)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def no_network(request, monkeypatch):
    """Make "no network in the default run" structural rather than conventional.

    Every provider reaches the outside world through `providers._get_bytes`, so
    severing that one function severs all of them. Before the provider seam the
    rule held only because each test remembered to install a fake `DDGS`; a
    provider that forgot would have started making real calls in CI, and the
    symptom would have been a slow, flaky suite rather than an obvious failure.

    Live tests opt back in through the `live` marker.
    """
    if "live" in request.keywords:
        return

    import providers

    def blocked(url, params):
        raise AssertionError(
            f"test tried to reach the network: {url}. Stub the provider, or mark "
            f"the test @pytest.mark.live if it is meant to call out.")

    monkeypatch.setattr(providers, "_get_bytes", blocked)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """A fresh, initialised SQLite store scoped to one test.

    Patches `db.DB_PATH` rather than the environment because `_db()` reads the
    module global on every connection — so the redirect takes effect immediately
    and unwinds cleanly.
    """
    import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    return db


@pytest.fixture
def fake_results():
    """A realistic ddgs response, including the messiness worth testing.

    Two entries collide after normalisation (`www.` + tracking params), one uses
    the legacy `url` key instead of `href`, and one is a duplicate of the first.
    """
    return [
        {"href": "https://www.cloudera.com/products/cdp.html?utm_source=search"},
        {"href": "https://docs.cloudera.com/cdp/latest/index.html"},
        {"url": "https://blog.cloudera.com/iceberg-in-cdp/"},  # pre-rename key
        {"href": "https://www.cloudera.com/products/cdp.html?utm_source=search"},
        {"href": "https://community.cloudera.com/t5/Support/ct-p/support"},
    ]


@pytest.fixture
def client(monkeypatch, tmp_path, temp_db):
    """FastAPI test client with retrieval stubbed out.

    No test in this suite may touch the network — a suite that fails because
    DuckDuckGo is rate-limiting is not testing this accelerator.
    """
    from fastapi.testclient import TestClient

    from app import server

    monkeypatch.setattr(server.db, "DB_PATH", temp_db.DB_PATH)
    # Redirect the Store button away from the developer's real backups/ before
    # any test can press it. Done here rather than per-test for the same reason
    # as `no_network`: a route test that forgot would litter the working tree
    # with snapshots, and nothing would fail to say so.
    monkeypatch.setattr(server.backup, "DEFAULT_DIR", tmp_path / "backups")

    def fake_search(text, **kwargs):
        fake_search.calls.append({"text": text, **kwargs})
        return [f"https://example.com/{text.replace(' ', '-')}/{i}" for i in range(3)]

    fake_search.calls = []
    monkeypatch.setattr(server.urlvestigia, "text_to_urls", fake_search)

    test_client = TestClient(server.app)
    test_client.search_calls = fake_search.calls
    return test_client
