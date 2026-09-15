"""Where the Serve layer should listen, where a browser can reach it, and what is
already on the port.

On a laptop the first two are the same question and the answer is `127.0.0.1:8000`.
In a **Cloudera AI (CML) Workbench session** they are not: the notebook runs inside
a container and the browser is outside it, so a link to `127.0.0.1` points the
browser at the *reader's own machine* and fails with nothing to diagnose. Cloudera
proxies one port per session — the one named by `CDSW_APP_PORT` — and publishes it
on a per-session subdomain built from `CDSW_ENGINE_ID` and `CDSW_DOMAIN`.

Two consequences, and both are why this is a module rather than four lines in a
notebook cell:

* the server has to bind `0.0.0.0` there, because the proxy reaches it from
  outside the loopback interface — and **only** there. Binding `0.0.0.0` on a
  laptop would put an application with no authentication and no CSRF protection on
  the local network, which is the one thing the README tells you not to do.
* the port is the platform's choice, not ours.

The third question belongs to whoever is about to hand someone a link. A port that
answers is not the same thing as *this* dashboard answering: it can be an unrelated
application, or a dashboard left behind by a kernel that has since been restarted —
which serves the code as it was when that kernel was alive, out of a process no
later kernel holds a handle to. `held()`, `identify()` and `edited_since()` are how
a caller tells those apart before printing a URL as though it were fresh.

`dashboard()`, `stop()` and `status()` are the process itself, kept here rather
than in the notebook that drives them: starting a server, deciding not to, and
saying which — with the reason behind each — is this module's subject, and a
notebook cell is a poor place for anything that has to be tested.

Every value is read from the environment at call time rather than at import, so a
notebook that sets one and re-runs a cell sees the change.
"""

import atexit
import html
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

DEFAULT_PORT = 8000
LOOPBACK = "127.0.0.1"
ALL_INTERFACES = "0.0.0.0"  # noqa: S104 - deliberate, and only when hosted

NAME = "source_ledger"  # what /healthz answers with, so a link is recognised not guessed
ROOT = Path(__file__).resolve().parent.parent
# The layers the Serve layer imports and renders. A change to any of them is a
# change a running server has not picked up; the rest of the repo can move without
# altering what a browser is being shown.
SOURCE = ("app", "data", "retrieval")
SOURCE_SUFFIXES = (".py", ".html", ".css", ".js")


def hosted():
    """Is this running inside a Cloudera AI session rather than on a laptop?

    Either variable is enough. `CDSW_APP_PORT` is the one that changes behaviour;
    `CDSW_ENGINE_ID` is checked too so a session that exposes no app port is still
    recognised as a session rather than silently treated as a laptop.
    """
    return bool(os.environ.get("CDSW_APP_PORT") or os.environ.get("CDSW_ENGINE_ID"))


def port(default=DEFAULT_PORT):
    """The port to listen on: the platform's choice when there is one."""
    configured = os.environ.get("CDSW_APP_PORT", "").strip()
    # Not int(...) in a try: a non-numeric value here means something has changed
    # about the platform, and silently falling back to 8000 would hand the reader a
    # link to a port nothing is proxying.
    return int(configured) if configured.isdigit() else default


def host():
    """The interface to bind. Loopback unless a proxy has to reach us."""
    return ALL_INTERFACES if hosted() else LOOPBACK


def url(port_number=None):
    """The address to put in front of a human.

    In a session this is the proxied subdomain and carries no port — Cloudera maps
    it to `CDSW_APP_PORT` for us. When the session variables are incomplete this
    falls back to the loopback address, which is wrong for a browser outside the
    container but is at least correct for anything inside it; `reachable()` is how
    a caller knows to explain itself.
    """
    engine = os.environ.get("CDSW_ENGINE_ID", "").strip()
    domain = os.environ.get("CDSW_DOMAIN", "").strip()
    if hosted() and engine and domain:
        return f"https://{engine}.{domain}/"
    return f"http://{LOOPBACK}:{port() if port_number is None else port_number}/"


def reachable():
    """Can the URL above actually be opened from outside this process?

    False in a session whose proxy address cannot be derived — the one case where
    the link is worth apologising for rather than printing plainly.
    """
    return not hosted() or url().startswith("https://")


def uvicorn_argv(app="app.server:app"):
    """The arguments to run this app with, wherever it is running."""
    return ["-m", "uvicorn", app, "--host", host(), "--port", str(port())]


# --- what is already on the port -------------------------------------------

def held(port_number=None, timeout=0.4):
    """Is anything at all holding this port?

    Always asked over loopback even when the server is bound to every interface:
    the caller runs in the same container, and the question is whether the port is
    taken, not whether the outside world can get to it.

    Kept as a plain socket next to `identify()` because the two cases it has to
    separate both look like silence over HTTP — a port held by something that is
    not this app, and one held by a build of this app that predates `/healthz`.
    Starting a server on either produces a uvicorn that exits with a bind error
    the caller has usually sent to DEVNULL.
    """
    target = port() if port_number is None else port_number
    with socket.socket() as probe:
        probe.settimeout(timeout)
        return probe.connect_ex((LOOPBACK, target)) == 0


def identify(port_number=None, timeout=1.0):
    """Ask whatever is on this port who it is: its `/healthz` answer, or None.

    None covers every way the question can fail to produce a dashboard this caller
    can vouch for — nothing listening, not an HTTP server, no such route, not JSON,
    or some other application's JSON. The pid in a real answer is what makes a
    server nobody holds a handle to stoppable anyway, and the `started` stamp is
    what `edited_since()` measures staleness against.
    """
    target = port() if port_number is None else port_number
    try:
        with urllib.request.urlopen(  # noqa: S310 - fixed scheme, loopback host
                f"http://{LOOPBACK}:{target}/healthz", timeout=timeout) as answer:
            reported = json.loads(answer.read(4096))
    except (OSError, ValueError):
        return None
    if isinstance(reported, dict) and reported.get("app") == NAME:
        return reported
    return None


def edited_since(when, root=ROOT):
    """Files the Serve layer loads that changed since `when`, most recent first.

    The one question a reused server cannot answer for itself. A process that
    started before your last edit serves the code as it was then, and the page it
    returns says nothing about it — which costs an afternoon the first time it
    happens and a restart every time after that.
    """
    changed = []
    for layer in SOURCE:
        for path in (root / layer).rglob("*"):
            if path.suffix not in SOURCE_SUFFIXES or "__pycache__" in path.parts:
                continue
            try:
                edited = path.stat().st_mtime
            except OSError:  # removed between the walk and the question
                continue
            if edited > when:
                changed.append((edited, path.relative_to(root).as_posix()))
    return [name for _, name in sorted(changed, reverse=True)]


# --- the dashboard as a process --------------------------------------------

UNREACHABLE = (
    "This is a Cloudera AI session, but CDSW_ENGINE_ID and CDSW_DOMAIN are not "
    "both set, so the proxied address cannot be derived here. The app is up and "
    "bound correctly — open it with the session's own web UI access for this port.")


def _p(text, style=""):
    return f'<p style="margin:.25em 0;{style}">{html.escape(text)}</p>'


class Dashboard:
    """What is on the port, and what can be done about it.

    `state` is one of:

    * **started** — this call launched it.
    * **mine** — a process the caller launched earlier, still alive.
    * **adopted** — a dashboard on the port that something else started. No handle
      to it survives a kernel restart; the pid it reports is the only one there is.
    * **foreign** — something holds the port and will not say it is this app.
    * **failed** — nothing came up, and `diagnose()` goes and asks why.
    """

    def __init__(self, process=None, server=None, state="failed", root=ROOT):
        self.process = process
        self.server = server      # the /healthz answer, when there is one
        self.state = state
        self.root = root

    @property
    def alive(self):
        """A live handle beats a live port: a socket can still answer for a moment
        after the server owning it was told to stop."""
        return self.process is not None and self.process.poll() is None

    def diagnose(self):
        """Why nothing came up — almost always an import error in the Serve layer.

        Asked for directly rather than left in the log that was sent to DEVNULL.
        """
        check = subprocess.run([sys.executable, "-c", "import app.server"],
                               cwd=str(self.root), capture_output=True, text=True)
        if check.returncode:
            return check.stderr.strip()[-1000:]
        return (f"It imports cleanly, so something else took port {port()} while it "
                f"was starting. Start it by hand to see what it says:\n"
                f"    {sys.executable} " + " ".join(uvicorn_argv()[1:]))

    def report(self):
        """The whole situation as HTML, for a notebook to display."""
        if self.state == "failed":
            return (_p(f"The dashboard did not come up on port {port()}.")
                    + '<pre style="font-size:.85em;white-space:pre-wrap">'
                    + html.escape(self.diagnose()) + "</pre>")

        out = []
        if self.state == "started":
            out.append(_p("Dashboard started."))
        elif self.state == "mine":
            out.append(_p("Already running — started by this notebook earlier."))
        elif self.state == "adopted":
            loaded = datetime.fromtimestamp(self.server["started"]).strftime("%H:%M")
            out.append(_p(f"Reusing the dashboard on this port — pid "
                          f"{self.server['pid']}, serving the code as of {loaded}."))
            out.append(_p("Started outside this kernel; the stop cell can still "
                          "stop it.", "opacity:.7"))
        else:
            out.append(_p("Reusing the server already on this port."))
            out.append(_p("It does not answer as this dashboard, so the link below "
                          "may be something else.", "opacity:.7"))
        out.append(_p(f"listening on {host()}:{port()}", "opacity:.7"))

        stale = edited_since(self.server["started"]) if self.state == "adopted" else []
        if stale:
            more = f" (+{len(stale) - 1} more)" if len(stale) > 1 else ""
            out.append(_p(f"{stale[0]}{more} changed after that server loaded its "
                          "code, so this is not your current edit — stop it and "
                          "re-run this cell.",
                          "border-left:3px solid rgba(200,140,0,.8);padding-left:.6em"))

        if reachable():
            link = url()
            out.append(f'<p style="font-size:1.1em;margin:.5em 0">&#127760; '
                       f'<a href="{link}" target="_blank" rel="noopener">{link}</a></p>'
                       + _p("Ctrl-click, or paste it into a browser.", "opacity:.7"))
        else:
            out.append(_p(UNREACHABLE, "opacity:.7"))
        return "".join(out)


def _launch(root, wait_s):
    """Start uvicorn in the background and wait for it to bind the port.

    Popen, not uvicorn.run(): the server has to outlive the cell that started it,
    and a blocking call there would hang the kernel with no way to reach the
    browser. No --reload either — the reloader serves from a grandchild process,
    which survives terminate() and holds the port after the caller says it stopped.
    """
    process = subprocess.Popen([sys.executable, *uvicorn_argv()], cwd=str(root),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    atexit.register(process.terminate)  # nothing outlives the kernel holding the port
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline and process.poll() is None and not held():
        time.sleep(0.25)
    return process


def dashboard(previous=None, root=ROOT, wait_s=25):
    """Reuse, adopt, or start — in that order, and never fight for the port.

    `previous` is whatever the caller's last run left behind, so re-running a cell
    keeps the process it already has instead of spawning a second one.
    """
    if previous is not None and previous.alive:
        return Dashboard(previous.process, identify(), "mine", root)
    server = identify()
    if server is not None:
        return Dashboard(None, server, "adopted", root)
    if held():
        return Dashboard(None, None, "foreign", root)
    process = _launch(root, wait_s)
    return Dashboard(process, identify(), "started" if held() else "failed", root)


def status(previous=None):
    """One line on what is on the port, for a stop cell that was told not to."""
    if (previous is not None and previous.alive) or identify() is not None:
        return f"Still running at {url()}"
    if held():
        return f"Something is on port {port()}, but it does not answer as this app."
    return f"Nothing is running on port {port()}."


def stop(previous=None, timeout=10):
    """Stop the dashboard, whoever started it. Returns what happened.

    Never stops a process it cannot name: something holding the port without
    answering as this app is somebody else's, and a notebook that kills unnamed
    processes eventually kills the wrong one.
    """
    if previous is not None and previous.alive:
        previous.process.terminate()
        try:
            previous.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            previous.process.kill()
        return "Dashboard stopped."

    server = identify()
    if server is not None:
        # os.kill for want of a handle — the same SIGTERM, and on Windows the same
        # TerminateProcess that Popen.terminate() calls.
        os.kill(server["pid"], signal.SIGTERM)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and held():
            time.sleep(0.25)
        if held():
            return (f"Sent SIGTERM to pid {server['pid']}, but port {port()} is still "
                    "held — a --reload worker outlives the process asked to stop.")
        return (f"Stopped the dashboard on port {port()} (pid {server['pid']}), "
                "which was started outside this notebook.")

    if held():
        return (f"Whatever is on port {port()} does not answer as this app, so it is "
                "left alone. Stop it where it was started.")
    return "Nothing to stop."
