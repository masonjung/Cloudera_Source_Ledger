"""Serve layer — where to listen, where to send a browser, and what is on the port.

The case that matters is a Cloudera AI session, where the notebook runs in a
container and the reader's browser does not. Getting this wrong produces the least
diagnosable failure in the whole accelerator: a link that looks right, resolves,
and connects to nothing — because `127.0.0.1` in a browser means the reader's own
laptop.

The security half is asserted just as hard: `0.0.0.0` in a session, never on a
laptop, because the app has no authentication.

The second half of the file is about the other thing a caller needs before it hands
someone a link: whether the server already on the port is this dashboard, somebody
else's application, or a dashboard from a kernel that has since been restarted —
and, for that last one, whether it predates the edit the reader just made.
"""

import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

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


# --- what is on the port ---------------------------------------------------

@pytest.fixture
def unused_port():
    """A port nothing is listening on — released again before it is handed over."""
    with socket.socket() as probe:
        probe.bind((hosting.LOOPBACK, 0))
        return probe.getsockname()[1]


@pytest.fixture
def answering():
    """Serve one canned response on an ephemeral loopback port, and return it.

    A real socket rather than a patched urlopen: what these tests are about is how
    a live port behaves — including a server that holds it while answering nothing
    useful — and that is precisely the part a stub would assume away.
    """
    servers = []

    def serve(body, status=200):
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass  # pytest's captured output is not the place for an access log

        server = HTTPServer((hosting.LOOPBACK, 0), Handler)
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server.server_address[1]

    yield serve
    for server in servers:
        server.shutdown()
        server.server_close()


def test_an_empty_port_holds_nothing_and_identifies_as_nobody(unused_port):
    assert hosting.held(unused_port) is False
    assert hosting.identify(unused_port) is None


def test_a_dashboard_identifies_itself(answering):
    """The pid is the point: it is what makes a server nobody holds a handle to
    stoppable, and the notebook's stop cell has nothing else to go on."""
    port = answering({"app": hosting.NAME, "pid": 4242, "started": 1000.0})

    assert hosting.identify(port) == {"app": hosting.NAME, "pid": 4242,
                                      "started": 1000.0}


@pytest.mark.parametrize("body, status", [
    ({"app": "someone-elses-app", "pid": 1}, 200),   # a different service
    (b"<html>hello</html>", 200),                    # not JSON at all
    (b'{"app": "urlvestigia"}', 404),                # no such route
], ids=["another app", "not json", "no healthz route"])
def test_only_this_dashboard_is_vouched_for(answering, body, status):
    """Every other answer means the same thing — this is not a server whose link
    can be handed over as the dashboard — so every one of them is None."""
    port = answering(body, status=status)

    assert hosting.identify(port) is None


def test_a_server_without_healthz_still_holds_the_port(answering):
    """The regression this pair of functions exists to prevent: a build of this app
    from before /healthz, or any other process, must not be mistaken for a free
    port. Starting a second server there exits with a bind error the notebook has
    sent to DEVNULL, which reads as "the dashboard did not come up" for no reason
    the reader can see.
    """
    port = answering(b"Not Found", status=404)

    assert hosting.identify(port) is None
    assert hosting.held(port) is True


# --- and whether it predates your last edit --------------------------------

STARTED = 1_000_000.0


def source_file(root, relative, edited):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# code", encoding="utf-8")
    os.utime(path, (edited, edited))
    return path


def test_only_files_newer_than_the_running_server_are_reported(tmp_path):
    source_file(tmp_path, "app/server.py", STARTED + 60)
    source_file(tmp_path, "data/db.py", STARTED - 60)

    assert hosting.edited_since(STARTED, root=tmp_path) == ["app/server.py"]


def test_the_most_recent_edit_is_named_first(tmp_path):
    """The caller prints one name and a count of the rest, so the name it prints
    has to be the edit the reader is currently wondering about."""
    source_file(tmp_path, "app/server.py", STARTED + 60)
    source_file(tmp_path, "retrieval/urlvestigia.py", STARTED + 120)
    source_file(tmp_path, "app/templates/index.html", STARTED + 30)

    assert hosting.edited_since(STARTED, root=tmp_path) == [
        "retrieval/urlvestigia.py", "app/server.py", "app/templates/index.html"]


def test_what_the_server_never_loads_is_not_a_stale_server(tmp_path):
    """A false alarm costs a restart of a server that was serving current code, so
    only the layers app/server.py imports and renders count. __pycache__ is written
    *by* the running server — reading it as an edit would make every server stale
    the moment it answered a request."""
    source_file(tmp_path, "tests/test_server.py", STARTED + 60)
    source_file(tmp_path, "docs/architecture.md", STARTED + 60)
    source_file(tmp_path, "app/__pycache__/server.py", STARTED + 60)

    assert hosting.edited_since(STARTED, root=tmp_path) == []


def test_a_server_started_after_every_edit_is_current(tmp_path):
    source_file(tmp_path, "app/server.py", STARTED - 1)

    assert hosting.edited_since(STARTED, root=tmp_path) == []
