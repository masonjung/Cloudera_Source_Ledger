"""URLvestigia Serve layer — everything is rendered server-side; no build step.

The template carries one inline progressive-enhancement script for the Search
button's in-flight state. Nothing here depends on it: with scripting off every
route behaves identically.

Run:  make dev   (or: uvicorn app.server:app --reload)
then open http://127.0.0.1:8000/
"""

import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(ROOT / "retrieval"))
sys.path.insert(0, str(ROOT / "data"))
# ROOT too, for `from app import hosting` below. Under `uvicorn app.server:app`
# the working directory already provides it; run as a script — which is exactly
# how a Cloudera AI Application starts this file — nothing does.
sys.path.insert(0, str(ROOT))

import backup
import db
import record
import urlvestigia
from app import hosting
from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

app = FastAPI(title="URLvestigia")
# When this process loaded its code, which is not the same as when it started
# answering and is exactly what /healthz is asked for. See that route.
STARTED = time.time()
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
db.init_db()

# Every route below turns an exception into a flash message, which is the right
# thing for the user and useless for whoever has to fix it: the message reaches a
# browser and the traceback reached nothing at all. These handlers log before they
# redirect, so a failure a colleague reports as "it said search failed" has a
# stack trace waiting on the server console. Uvicorn configures the root handler;
# nothing here needs its own.
log = logging.getLogger("urlvestigia.serve")

# The whitelists and labels the routes below enforce. They are defined in
# data/record.py, not here, because the dashboard is no longer the only thing that
# writes a search record — scripts/cli.py and quickstart.ipynb do too, and a
# whitelist restated per interface is a whitelist that eventually disagrees with
# itself. Aliased rather than referenced through `record.` so this module still
# reads as what enforces them, and so a template or a test can keep asking the
# server what it offers.
OPTIONS = record.OPTIONS
PROVIDER_LABELS = record.PROVIDER_LABELS
TOGGLEABLE = record.TOGGLEABLE


def _providers():
    """Provider id, label, and the options it does not apply.

    The template renders its radio pills and emits its control-visibility CSS from
    this, so the support matrix in retrieval/providers.py is never restated in markup
    where it could drift out of step with what the server enforces.
    """
    return [{
        "id": name,
        "label": PROVIDER_LABELS.get(name, name),
        "unsupported": [opt for opt in TOGGLEABLE
                        if opt not in urlvestigia.supports(name)],
    } for name in OPTIONS["provider"]]


def _redirect(msg=""):
    return RedirectResponse(f"/?msg={quote(msg)}" if msg else "/", status_code=303)


def _when(iso):
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%b %d %H:%M")
    except ValueError:
        return iso


def _rel(path):
    """Repo-relative when the path is inside the repo, absolute when it is not.

    `URLVESTIGIA_DB` and `URLVESTIGIA_BACKUP_DIR` can both point anywhere, so neither label
    can assume it is showing something under ROOT.
    """
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


@app.get("/")
def home(request: Request, msg: str = ""):
    rows = db.list_searches()
    for r in rows:
        r["when"] = _when(r["created_at"])
        # Always the engines as stored, never a summary. A full selection used to
        # render as "any", which was wrong three ways: it hid which engines were
        # asked, it read as the Time column's "any" (no time limit) in the
        # neighbouring cell, and because the test compared against the *current*
        # engine list, adding one silently re-labelled historical rows.
        r["backend_label"] = (r["backend"] or "").replace(",", "+") or None
        r["provider_label"] = PROVIDER_LABELS.get(r["provider"], r["provider"])
    return templates.TemplateResponse(request, "index.html", {
        "rows": rows, "msg": msg, "stats": db.stats(),
        "db_label": _rel(db.DB_PATH), "providers": _providers(),
    })


@app.get("/healthz")
def healthz():
    """Who is answering on this port — for whoever is about to hand out its link.

    A socket probe says the port is taken; it cannot say by what. Anything that
    finds a server already running — quickstart.ipynb, a deploy check — has three
    cases to separate: this dashboard, an unrelated application, and a dashboard
    left behind by a kernel that has since been restarted. The last one is the
    expensive one: it serves the code as it was then, out of a process nothing
    still holds a handle to, and it looks identical to a fresh start from the
    outside.

    Three facts answer all three, and none of them touch the database: the name to
    recognise, the pid that makes an orphan stoppable, and when this process loaded
    its code, which is what a caller compares its own edits against.
    """
    return {"app": hosting.NAME, "pid": os.getpid(), "started": STARTED}


@app.post("/search")
def search(
    text: str = Form(...),
    provider: str = Form("ddgs"),
    max_results: int = Form(10),
    timelimit: str = Form(""),
    backend: list[str] = Form([]),
    safesearch: str = Form("moderate"),
    region: str = Form("wt-wt"),
):
    text = text.strip()
    if not text:
        return _redirect()
    # Whitelisting, engine deduplication, and clamping all happen in one place now;
    # what comes back is both what gets searched with and what gets recorded.
    opts = record.normalize(
        provider=provider, max_results=max_results, timelimit=timelimit,
        safesearch=safesearch, region=region, backend=backend,
    )
    # Both failure branches below name the provider and the engines that were asked.
    provider, backend = opts["provider"], opts["backend"]
    try:
        # Every option is passed; text_to_urls drops the ones this provider does
        # not apply, reading the same matrix record.save() reads.
        urls = record.search(text, opts)
    except urlvestigia.EngineError as exc:
        log.warning("%s search failed: every engine errored (%s) for %r",
                    provider, exc, text)
        # Every engine failed and each said why. ddgs reports this as an empty
        # search, so without naming the engines it would reach the user as an
        # ordinary "No results found." — a dead network wearing the face of an
        # empty corpus, which is what made a failed demo unreadable from the room.
        where = PROVIDER_LABELS.get(provider, provider)
        detail = ", ".join(f"{engine}: {reason}" for engine, reason in exc.failures)
        return _redirect(f"Error: {where} search failed — no engine answered — {detail}")
    except Exception as exc:
        # exception() not warning(): unlike the branch above, this catches bugs as
        # well as outages, and a bug with no traceback is invisible.
        log.exception("%s search raised for %r", provider, text)
        # Name what failed. The exception text comes from whichever library made the
        # call and says nothing about which corpus was searched or, for a chain of
        # four engines, which of them was asked — so the bare message left the user
        # unable to tell a blocked engine from a broken query.
        where = PROVIDER_LABELS.get(provider, provider)
        if provider == "ddgs":
            where += f" ({backend.replace(',', ' + ')})"
        return _redirect(f"Error: {where} search failed — {exc}")
    if not urls:
        return _redirect("No results found.")
    # Record only what this provider actually applied. An unsupported option is
    # stored NULL rather than as the value the form happened to post — a Wikipedia
    # search stamped timelimit="w" would claim a filter that never ran. The rule
    # itself lives in data/record.py, where every interface reaches it.
    record.save(text, urls, opts)
    return _redirect(f'{len(urls)} result{"" if len(urls) == 1 else "s"} saved for "{text}"')


@app.post("/delete/{search_id}")
def delete(search_id: int):
    db.delete_search(search_id)
    return _redirect()


@app.post("/store")
def store():
    """Write a dated snapshot of the dev store to the local backups directory.

    Safe to press while searches are running — `db.backup()` uses SQLite's online
    backup API, not a file copy. Nothing here overwrites: a snapshot is only ever
    added, which is why this sits next to the destructive Clear all without
    needing a confirmation of its own.
    """
    try:
        written = backup.snapshot()
    except FileExistsError:
        # Two presses inside one second. Names are second-resolution, so the
        # second press genuinely stored nothing and must not report success.
        return _redirect("Error: a snapshot for this second already exists — "
                         "wait a moment and press Store again.")
    except Exception as exc:
        log.exception("backup failed")
        return _redirect(f"Error: backup failed — {exc}")
    # Counted out of the finished file rather than the live store: it proves the
    # snapshot opens as a database, which is the only part of "it worked" a
    # backup can meaningfully claim.
    rows = backup.counts(written)
    return _redirect(f"Stored {_rel(written)} — {rows['searches']} searches, "
                     f"{rows['search_urls']} URLs.")


@app.get("/download")
def download():
    """Hand the browser a snapshot so the *browser* chooses where it lands.

    "Pick the folder" cannot mean a directory picker for the server's disk: no
    browser exposes one, and a web form that chooses arbitrary write paths on the
    host would be a hole rather than a feature. It stays wrong once `deploy.sh
    app` puts this behind Cloudera AI, where the server's disk is a container
    nobody is sitting at. Sending the file instead moves the choice to the Save As
    dialog, which is the machine the user actually wanted all along.

    Deliberately not a redirect to a stored snapshot: Store is a server-side
    backup that is never overwritten, and downloading must not litter backups/
    with a file per click. This writes to a temp directory that the response
    deletes once it has been sent.

    Written through db.backup() rather than served from DB_PATH directly, for the
    reason that function documents — the app holds the database open, so shipping
    the live file could transmit a torn page.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="urlvestigia-download-"))
    cleanup = BackgroundTask(shutil.rmtree, tmp_dir, ignore_errors=True)
    try:
        snapshot = db.backup(tmp_dir / backup.default_name())
    except FileNotFoundError:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return _redirect("Error: nothing to download yet — run a search first.")
    except Exception as exc:
        log.exception("download snapshot failed")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return _redirect(f"Error: download failed — {exc}")
    # `filename` is what sets Content-Disposition: attachment, which is the whole
    # mechanism — without it a browser renders the bytes instead of saving them.
    return FileResponse(snapshot, media_type="application/vnd.sqlite3",
                        filename=snapshot.name, background=cleanup)


@app.post("/dedupe")
def dedupe():
    """Collapse duplicate URLs, and say so when searches went with them.

    `db.dedupe_urls()` also deletes any search left holding no URLs, and that is
    the part worth naming: a search record is the artifact this app exists to
    keep, so a button labelled "dedupe URLs" must not remove one silently. The
    count is taken either side of the call rather than returned, which leaves
    dedupe_urls()'s contract -- the number of URL rows removed -- untouched.
    """
    before = db.stats()["searches"]
    removed = db.dedupe_urls()
    if not removed:
        return _redirect("No duplicate URLs found.")
    msg = f'Removed {removed} duplicate URL{"" if removed == 1 else "s"}'
    searches = before - db.stats()["searches"]
    if searches:
        msg += (f' and {searches} search{"" if searches == 1 else "es"} '
                "left with none")
    return _redirect(msg + ".")


@app.post("/clear")
def clear():
    db.clear_all()
    return _redirect()


if __name__ == "__main__":
    # A Cloudera AI Application runs `python app/server.py` — see the
    # `--script app/server.py` in .cicd/deploy.sh — not a uvicorn command line.
    # Without this the platform would start the file, import it, define `app`,
    # exit 0, and report a deployment that never listened on anything.
    import uvicorn

    # The same binding rule as everywhere else, and for the same reason: every
    # interface only where a proxy has to reach us. app/hosting.py owns it.
    uvicorn.run(app, host=hosting.host(), port=hosting.port())
