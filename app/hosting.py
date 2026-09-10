"""Where the Serve layer should listen, and where a browser can reach it.

On a laptop those are the same question and the answer is `127.0.0.1:8000`. In a
**Cloudera AI (CML) Workbench session** they are not: the notebook runs inside a
container and the browser is outside it, so a link to `127.0.0.1` points the
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

Every value is read from the environment at call time rather than at import, so a
notebook that sets one and re-runs a cell sees the change.
"""

import os

DEFAULT_PORT = 8000
LOOPBACK = "127.0.0.1"
ALL_INTERFACES = "0.0.0.0"  # noqa: S104 - deliberate, and only when hosted


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
