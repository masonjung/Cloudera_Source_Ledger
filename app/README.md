# `app/` — Serve layer

The reference front-end. This is what a stakeholder actually opens, and the only
layer with a UI.

URLvestigia's Serve layer is **FastAPI + Jinja2, rendered entirely server-side with no
build step and no framework**. That is a deliberate choice, not a gap: the whole
accelerator installs with `make install` and runs with one command, which keeps the
Discover → Qualify demo loop short.

The single exception is ~40 lines of inline script that put the Search button into a
spinning "Searching… 12s" state while the post is in flight — a search blocks for up
to ~38s, and without it the page looks hung. It is progressive enhancement: with
scripting off the form posts exactly as before. Reasoning in
[`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).

## What's here

| Path | What it is |
|---|---|
| `server.py` | The FastAPI app: renders the page, handles form posts, validates every option against a whitelist |
| `hosting.py` | Where to listen and where to send a browser — loopback locally, the proxied port in a Cloudera AI session |
| `templates/index.html` | The entire UI — one Jinja2 template: HTML, CSS, and one inline progressive-enhancement script |
| `requirements.txt` | This layer's dependencies, pulling in `retrieval/` since `server.py` imports it |
| `__init__.py` | Makes `app` importable so `uvicorn app.server:app` resolves |

Dependencies live with their layer, the same way the Forge template keeps
`package.json` inside `app/`. Because the Serve layer pulls in the retrieval layer,
`app/requirements.txt` plus `tests/requirements.txt` is the full closure — that is
what `make install` installs.

## Run it

```bash
make dev                                  # → http://127.0.0.1:8000/
uvicorn app.server:app --reload           # same thing, without make
```

## How it wires to the other layers

`server.py` puts `retrieval/` and `data/` on the import path, then calls into them:

```
app/server.py
  ├── import urlvestigia   → retrieval/urlvestigia.py   (retrieval layer: text → ranked URLs)
  └── import db      → data/db.py           (Lakehouse layer: persist searches + URLs)
```

The Serve layer holds **no business logic and no SQL**. Retrieval belongs to
`retrieval/`, persistence belongs to `data/`. If you find yourself writing a query here,
it belongs in `data/db.py`.

## Conventions

- **POST-redirect-GET everywhere.** Every mutation (`/search`, `/delete/{id}`,
  `/dedupe`, `/clear`) ends in a 303 back to `/` carrying a `?msg=` flash. Reloading
  never re-runs a search.
- **One route that is not a page.** `/healthz` answers with the app name, this
  process's pid, and when it loaded its code. A socket probe can only say that a
  port is taken — not whether what holds it is this dashboard, an unrelated
  application, or a dashboard a restarted kernel left behind still serving
  pre-edit code. `quickstart.ipynb` tells those apart with this route, and stops
  the last one with the pid. See [`app/hosting.py`](hosting.py).
- **Whitelist every input.** `OPTIONS` in [`data/record.py`](../data/record.py) is
  the single source of truth for allowed `timelimit` / `backend` / `safesearch` /
  `region` values, re-exported here as `server.OPTIONS`; `record.normalize()`
  falls back to the first entry and clamps `max_results` to 1–50. Nothing
  user-supplied reaches the search engines unchecked — and because the CLI and the
  notebook normalise through the same function, none of the three can drift.
- **One template.** Styles live in the `<style>` block in `index.html`. The design
  tokens are the `:root` custom properties — see
  [`docs/architecture/DESIGN_TEMPLATE.md`](../docs/architecture/DESIGN_TEMPLATE.md).

## Which Cloudera tool automates it

Deployed as a **Cloudera AI Application** (formerly CML Applications) — a
long-running hosted web service inside the AI Workbench, fronted by Knox and
authenticated through SDX. `infra/cdp/provision.sh` creates the workspace;
`.cicd/deploy.sh` registers and restarts the application.

Because it is a plain ASGI app, it also runs unchanged in any container runtime —
useful for local demos before a CDP environment exists.

**Where it binds is not a constant.** On a laptop it is `127.0.0.1:8000`; inside a
Cloudera AI session the browser is outside the container, so the app has to bind
every interface on the port the platform proxies (`CDSW_APP_PORT`) and is reached
at a subdomain built from `CDSW_ENGINE_ID` and `CDSW_DOMAIN`. `hosting.py` decides
which, and binds `0.0.0.0` **only** in a session — doing it on a laptop would put
an app with no authentication and no CSRF protection on the local network.
`quickstart.ipynb` starts the server through it; `make dev` is the laptop loop and
stays on loopback with `--reload`.

## If you swap in a JavaScript front-end

The Forge template's reference app is React 19 + TypeScript + Tailwind + Vite. If
this accelerator moves that way, keep `server.py` as the JSON API, add the SPA
under `app/web/`, and point `make dev` at both. Nothing in `retrieval/`, `data/`,
`pipelines/`, or `governance/` should need to change — that is the test of whether
the layering held.
