"""Serve layer — where to listen, and where to send a browser.

The case that matters is a Cloudera AI session, where the notebook runs in a
container and the reader's browser does not. Getting this wrong produces the least
diagnosable failure in the whole accelerator: a link that looks right, resolves,
and connects to nothing — because `127.0.0.1` in a browser means the reader's own
laptop.

The security half is asserted just as hard: `0.0.0.0` in a session, never on a
laptop, because the app has no authentication.
"""

import pytest
from app import hosting

CDSW_VARS = ("CDSW_APP_PORT", "CDSW_ENGINE_ID", "CDSW_DOMAIN")


@pytest.fixture(autouse=True)
def laptop(monkeypatch):
    """Start every test on a laptop, whatever the machine running the suite is.

    Without this the suite would pass or fail depending on where it runs, which is
    exactly the confusion these tests exist to remove.
    """
    for name in CDSW_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def session(monkeypatch):
    """A Cloudera AI session, as the platform sets it up."""
    def configure(port="8100", engine="abc123xyz", domain="ml.example.cloudera.site"):
        for name, value in zip(CDSW_VARS, (port, engine, domain)):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
    return configure


# --- on a laptop -----------------------------------------------------------

def test_a_laptop_binds_loopback_only():
    """The app has no authentication and no CSRF protection. Binding every
    interface would put it on the local network — see the README's warning."""
    assert hosting.host() == "127.0.0.1"
    assert hosting.hosted() is False


def test_a_laptop_uses_port_8000():
    assert hosting.port() == 8000
    assert hosting.url() == "http://127.0.0.1:8000/"
    assert hosting.reachable() is True


# --- in a Cloudera AI session ----------------------------------------------

def test_a_session_binds_every_interface(session):
    """The proxy reaches the app from outside the loopback interface, so loopback
    would leave the published URL answering nothing."""
    session()

    assert hosting.hosted() is True
    assert hosting.host() == "0.0.0.0"


def test_a_session_listens_on_the_port_the_platform_proxies(session):
    session(port="8100")

    assert hosting.port() == 8100


def test_a_session_publishes_the_proxied_subdomain_without_a_port(session):
    """Cloudera maps the subdomain to CDSW_APP_PORT, so naming the port in the URL
    would send the browser to a port the proxy does not serve."""
    session(engine="abc123xyz", domain="ml.example.cloudera.site")

    assert hosting.url() == "https://abc123xyz.ml.example.cloudera.site/"
    assert hosting.reachable() is True


def test_a_session_without_a_derivable_address_says_so(session):
    """Rather than printing a loopback link as though a browser could open it."""
    session(domain=None)

    assert hosting.hosted() is True
    assert hosting.reachable() is False


def test_a_non_numeric_app_port_falls_back_rather_than_raising(session):
    """A platform change should not take the notebook down with a ValueError."""
    session(port="not-a-port")

    assert hosting.port() == hosting.DEFAULT_PORT


def test_the_environment_is_read_at_call_time_not_at_import(session):
    """A notebook cell that sets a variable and re-runs must see the change."""
    assert hosting.host() == "127.0.0.1"
    session()
    assert hosting.host() == "0.0.0.0"


# --- the command it produces -----------------------------------------------

def test_uvicorn_argv_carries_both_the_host_and_the_port(session):
    session(port="8100")

    assert hosting.uvicorn_argv() == [
        "-m", "uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8100"]


def test_uvicorn_argv_on_a_laptop_stays_on_loopback():
    assert hosting.uvicorn_argv() == [
        "-m", "uvicorn", "app.server:app", "--host", "127.0.0.1", "--port", "8000"]
