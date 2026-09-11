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


def test_the_server_is_never_started_with_reload():
    """--reload serves from a grandchild process, which survives terminate() and
    holds the port after the caller has said it stopped."""
    assert "--reload" not in hosting.uvicorn_argv()


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


# --- reuse, adopt, or start ------------------------------------------------

class FakeProcess:
    """A Popen stand-in that is alive until something stops it."""

    def __init__(self, alive=True, pid=4242):
        self.pid = pid
        self.alive = alive
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated = True
        self.alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True
        self.alive = False


@pytest.fixture
def port_state(monkeypatch):
    """Drive what the port looks like without binding one.

    The decision this module makes — reuse, adopt, start, or refuse — is the part
    worth testing, and starting real servers to test it would make the suite slow,
    order-dependent, and dependent on port 8000 being free on the machine running it.
    """
    state = {"held": False, "server": None, "launched": []}

    def launch(root, wait_s):
        state["launched"].append(root)
        state["held"] = True
        state["server"] = {"app": hosting.NAME, "pid": 999, "started": 1000.0}
        return FakeProcess()

    monkeypatch.setattr(hosting, "held", lambda *args, **kwargs: state["held"])
    monkeypatch.setattr(hosting, "identify", lambda *args, **kwargs: state["server"])
    monkeypatch.setattr(hosting, "_launch", launch)
    return state


def healthz(pid=4242, started=1000.0):
    return {"app": hosting.NAME, "pid": pid, "started": started}


def test_an_empty_port_gets_a_server(port_state):
    board = hosting.dashboard()

    assert board.state == "started"
    assert port_state["launched"] == [hosting.ROOT]


def test_re_running_keeps_the_process_it_already_has(port_state):
    """The cell is meant to be re-run. Spawning a second server each time would
    leave one of them holding the port and the other exiting with a bind error."""
    first = hosting.dashboard()
    port_state["launched"].clear()

    again = hosting.dashboard(first)

    assert again.state == "mine"
    assert again.process is first.process
    assert port_state["launched"] == []


def test_a_server_a_restarted_kernel_left_behind_is_adopted(port_state):
    """No handle survives a restart, so without /healthz this was indistinguishable
    from a fresh start — while serving whatever the code said an hour ago."""
    port_state["held"] = True
    port_state["server"] = healthz(pid=4242)

    board = hosting.dashboard()

    assert board.state == "adopted"
    assert board.server["pid"] == 4242
    assert port_state["launched"] == []


def test_a_port_held_by_something_else_is_reused_not_fought_for(port_state):
    """VS Code's tasks.json starts uvicorn on folderOpen, so a server on the port
    is the normal case for this repo's own developers. Starting a second one would
    exit with a bind error into DEVNULL and report "the dashboard did not come up"."""
    port_state["held"] = True
    port_state["server"] = None  # answers a probe, but not as this app

    board = hosting.dashboard()

    assert board.state == "foreign"
    assert port_state["launched"] == []


def test_a_server_that_never_binds_is_reported_as_failed(port_state, monkeypatch):
    monkeypatch.setattr(hosting, "_launch", lambda root, wait_s: FakeProcess())

    assert hosting.dashboard().state == "failed"


def test_a_dead_handle_is_not_a_running_server(port_state):
    """The kernel outlives the process it points at often enough to matter."""
    dead = hosting.Dashboard(FakeProcess(alive=False), None, "started")

    assert dead.alive is False
    assert hosting.dashboard(dead).state == "started"  # started a replacement


# --- stopping it -----------------------------------------------------------

def test_stop_terminates_the_process_it_holds(port_state):
    board = hosting.dashboard()

    message = hosting.stop(board)

    assert board.process.terminated
    assert "stopped" in message.lower()


def test_stop_kills_a_server_it_never_started(port_state, monkeypatch):
    """After a kernel restart the pid from /healthz is the only handle there is,
    and without this the notebook's own stop instruction is false."""
    port_state["held"] = True
    port_state["server"] = healthz(pid=4242)
    killed = []

    def kill(pid, signal_number):
        killed.append(pid)
        port_state["held"] = False
        port_state["server"] = None

    monkeypatch.setattr(hosting.os, "kill", kill)

    message = hosting.stop()

    assert killed == [4242]
    assert "4242" in message


def test_stop_never_kills_a_process_it_cannot_name(port_state, monkeypatch):
    """Something holding the port without answering as this app is somebody else's.
    A notebook that stops unnamed processes eventually stops the wrong one."""
    port_state["held"] = True
    port_state["server"] = None
    monkeypatch.setattr(hosting.os, "kill",
                        lambda *args: pytest.fail("killed an unidentified process"))

    assert "left alone" in hosting.stop()


def test_stop_says_when_the_port_outlives_the_process_it_stopped(port_state, monkeypatch):
    """A --reload worker is a grandchild: SIGTERM to the parent leaves it serving."""
    port_state["held"] = True
    port_state["server"] = healthz(pid=4242)
    monkeypatch.setattr(hosting.os, "kill", lambda *args: None)  # nothing releases it

    assert "still held" in hosting.stop(timeout=0)


def test_stopping_nothing_says_so(port_state):
    assert hosting.stop() == "Nothing to stop."


def test_status_reports_a_server_this_session_never_started(port_state):
    port_state["held"] = True
    port_state["server"] = healthz()

    assert "Still running" in hosting.status()


def test_status_of_an_empty_port(port_state):
    assert "Nothing is running" in hosting.status()


# --- what it reports -------------------------------------------------------

def test_the_report_names_the_pid_and_when_its_code_was_loaded(monkeypatch):
    monkeypatch.setattr(hosting, "edited_since", lambda when: [])
    board = hosting.Dashboard(None, healthz(pid=4242), "adopted")

    report = board.report()

    assert "4242" in report
    assert "http://127.0.0.1:8000/" in report


def test_the_report_warns_when_the_running_server_predates_your_edit(monkeypatch):
    """The failure that produces a bug report about a fix that "did nothing"."""
    monkeypatch.setattr(hosting, "edited_since",
                        lambda when: ["app/server.py", "data/db.py"])
    board = hosting.Dashboard(None, healthz(), "adopted")

    report = board.report()

    assert "app/server.py" in report
    assert "+1 more" in report


def test_a_started_server_is_not_checked_for_staleness(monkeypatch):
    """It loaded its code a second ago; every edit in the repo predates it."""
    monkeypatch.setattr(hosting, "edited_since",
                        lambda when: pytest.fail("asked about a server it just started"))

    assert "Dashboard started" in hosting.Dashboard(FakeProcess(), healthz(),
                                                    "started").report()


def test_the_report_does_not_hand_out_a_link_a_browser_cannot_follow(session):
    """A session whose proxy address cannot be derived: the app is up and correct,
    and only the link is unknown. Saying which is the difference between a
    five-minute fix and a bug report."""
    session(domain=None)
    board = hosting.Dashboard(FakeProcess(), healthz(), "started")

    report = board.report()

    assert "<a href" not in report
    assert "CDSW_ENGINE_ID" in report


def test_a_foreign_server_is_reported_as_unvouched_for(port_state):
    report = hosting.Dashboard(None, None, "foreign").report()

    assert "may be something else" in report


def test_a_failure_reports_what_the_serve_layer_said(monkeypatch, tmp_path):
    """Almost always an import error, and the log went to DEVNULL — so it is asked
    for directly rather than left somewhere nobody will look."""
    board = hosting.Dashboard(FakeProcess(), None, "failed", root=tmp_path)

    report = board.report()

    assert "did not come up" in report
    assert "ModuleNotFoundError" in report  # no app package under tmp_path
