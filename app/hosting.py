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

Every value is read from the environment at call time rather than at import, so a
notebook that sets one and re-runs a cell sees the change.
"""

import json
import os
import socket
import urllib.request
from pathlib import Path

DEFAULT_PORT = 8000
LOOPBACK = "127.0.0.1"
ALL_INTERFACES = "0.0.0.0"  # noqa: S104 - deliberate, and only when hosted

NAME = "urlvestigia"  # what /healthz answers with, so a link is recognised not guessed
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
