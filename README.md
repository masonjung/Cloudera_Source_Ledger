# Source Ledger

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](pyproject.toml)
[![Cloudera Blueprint](https://img.shields.io/badge/Cloudera-Blueprint-f96702.svg)](METADATA.yaml)
[![Stars](https://img.shields.io/github/stars/masonjung/Cloudera_Source_Ledger?logo=github)](https://github.com/masonjung/Cloudera_Source_Ledger/stargazers)
[![Forks](https://img.shields.io/github/forks/masonjung/Cloudera_Source_Ledger?logo=github)](https://github.com/masonjung/Cloudera_Source_Ledger/network/members)
[![Watchers](https://img.shields.io/github/watchers/masonjung/Cloudera_Source_Ledger?logo=github)](https://github.com/masonjung/Cloudera_Source_Ledger/watchers)

<div align="center">
  <img width="512" height="280" alt="image" src="https://github.com/user-attachments/assets/263b66db-ec42-4bb0-a3f9-79f8ba33c8ee" />
</div>


Source Ledger turns a search prompt into a queryable table of source URLs — so a search becomes an artifact instead of an activity. On the web browser, what we can get from the search is URLs that we could click to access to the website. It does not store the searched URLs nor search hyperparameters. Also, we do not know about the type of the engine (e.g., Brave Search, Google Search, DuckDuckGo search, Bing search etc.), scope, and other constraints (e.g., region, time, safe search) that we used, undermining the specificity and multiple features that contributed to the search results. 

Search results are usually blow away after the initial search. We do not remember what we searched for, and how it has been searched.  

We introduce Cloudera Source Ledger, which tabulates every search results as a governed record — the engine used, the query, URLs, and the search parameters applied — with heterogeneous search engines across multiple domains.
The program converts individual, siloed searching into a shared, queryable table the whole team can draw on. You can turn hours of repeated ad hoc research into a reusable asset and cuts redundant search costs. 

**Why Cloudera:** this is built for teams to reuse, not individuals to run once — the record is the product, not the search itself.



## Table of Contents

- [Overview](#overview)
- [Demo](#demo)
- [Use Case](#use-case)
- [Key Features](#key-features)
- [Quickstart / Guide](#quickstart--guide)
- [Architecture / Software Components](#architecture--software-components)
- [Target Audience](#target-audience)
- [Repository Structure](#repository-structure)
- [Prerequisites](#prerequisites)
- [Hardware Requirements](#hardware-requirements)
- [Documentation](#documentation)

## Demo

<img width="1153" height="841" alt="image" src="https://github.com/user-attachments/assets/4c6c6a0b-73d8-4924-88d8-dd6f70f21dd3" />
<img width="1126" height="812" alt="image" src="https://github.com/user-attachments/assets/c89f8fe8-7304-4bf0-bb3d-2f1757f6450f" />
<img width="1161" height="787" alt="image" src="https://github.com/user-attachments/assets/69e7b025-8742-4d3e-bd66-9e3039d3ae91" />



## Use Case


1. **Record.** Every search is captured, not lost — the query, the options, and the
   URLs it returned are written down at the moment the search runs, so nothing depends
   on someone's memory or browser history.

2. **Enables team work.** A search stops being one person's private activity and
   becomes something a team can pick up, review, or continue — discovery is handed off
   instead of re-run from scratch.

3. **Same format, queryable.** Every search lands in the same governed schema, so the
   whole history of searches — anyone's, any time — can be queried, filtered, and
   compared like any other table, not scattered across individual habits or tools.

4. **The synergy.** Together, a *record* that's *shared* and *uniformly queryable* turns
   search from a one-off task into organizational memory: the team doesn't just avoid
   repeating work, it can ask new questions of everything that's already been searched.

5. **URLs as intellectual property, not one-time usage.** A URL found once is a
   disposable click; a URL captured into the ledger is a reusable asset — attributable,
   corroborated across corpora, and worth owning rather than worth using once and
   losing.

## Key Features

- **One question, four corpora.** Reach the open web, an encyclopedia, the scholarly
  record, and preprints through a single call, without learning four APIs or writing
  per-source code.
- **A record that cannot overstate itself.** Each search is stored with exactly the
  options that were applied. Options a corpus does not support are recorded as `NULL`
  rather than as the value the form happened to carry, so the record never claims a
  filter that never ran.
- **Corroboration instead of duplicates.** A URL found by more than one corpus is
  retained as independent confirmation — a stronger signal than the same link twice from
  one engine.
- **A search survives a throttled engine.** Web queries go to four engines at once and
  pool their results, so one blocked or rate-limited engine no longer empties the
  search.
- **Three ways in, one record.** A dashboard for people who do not write code, a CLI
  that pipes, and a Run All notebook — all writing through the same
  [`data/record.py`](data/record.py), so the NULL rule above cannot hold in one
  interface and quietly lapse in another.
- **Nothing to buy, nothing to build.** No API keys, no accounts, no build step, and no
  front-end toolchain. It runs on a laptop with one command, and every page works with
  JavaScript disabled.
- **Demo failures found beforehand.** `make doctor` probes every corpus and every engine
  individually with timings, so a blocked network is discovered at your desk rather than
  in front of a customer.
- **Links only, never page content.** The blueprint records where an answer was found
  and never retrieves or stores the page itself, which keeps the governance surface
  small by construction.

## Quickstart / Guide

Everything here runs on a laptop, with nothing provisioned.

1. **Clone the repository.**

   ```bash
   git clone https://github.com/masonjung/URLvestigia && cd URLvestigia
   ```

2. **Install and run.**

   ```bash
   make install     # runtime + test dependencies
   make dev         # → http://127.0.0.1:8000/
   ```

3. **Before a live demo,** confirm this machine reaches every corpus:

   ```bash
   make doctor      # probes each provider and each engine, with latency
   ```

4. **Verify the build.**

   ```bash
   make test        # 346 passing, 13 skipped (the live tier, opt in with --live)
   ```

**Without `make`** — the Makefile needs bash, so on Windows without Git Bash or WSL run
the same commands directly:

```bash
python -m pip install -r app/requirements.txt -r tests/requirements.txt
python -m uvicorn app.server:app --reload --port 8000
python scripts/doctor.py
python -m pytest tests -q
```

### From the terminal

No server, no browser. The same governed rows, and URLs on stdout so they pipe:

```bash
python scripts/cli.py search "Cloudera AI inference service" --provider openalex
python scripts/cli.py search "iceberg compaction" --backend duckduckgo --timelimit y -n 25
python scripts/cli.py list --urls
python scripts/cli.py export --format csv --out review-appendix.csv
python scripts/cli.py doctor
```

`search` writes the URLs to stdout and everything else — which options this corpus
applied, which it ignored, where the row landed — to stderr, so
`search "..." > urls.txt` leaves a file of URLs and nothing else. Exit codes
distinguish the cases a script has to tell apart: `0` results, `1` the search failed,
`2` usage, `3` the corpus answered and had nothing. Full reference:
[`scripts/README.md`](scripts/README.md).

### From a notebook — no terminal at all

[`quickstart.ipynb`](quickstart.ipynb) is Run All, top to bottom, from a cold clone.
It checks what this network reaches, picks a corpus that answered, runs a search,
shows the record, queries it in SQL, and writes the export — every step in the
notebook itself, so nothing has to be repeated in a shell. It installs whatever the
kernel is missing as it goes, and a blocked network gets a named diagnosis rather
than a traceback. The dashboard is the one thing it does not start; that is
`make dev`, above.

```bash
make install-notebook    # or: pip install -r requirements-notebook.txt
make notebook            # or: jupyter lab quickstart.ipynb
```

### In a Cloudera AI Workbench session

The same notebook is the intended path in a **Cloudera AI (CML) session** — start
one with the JupyterLab editor on a Python 3.11 runtime, open `quickstart.ipynb`,
and Run All. 2 vCPU / 4 GiB is ample; there is no model here and no GPU is used.

Two things differ from a laptop, and the notebook handles both:

- **No port, no browser.** The notebook starts no server: section 4 renders the
  store in the notebook itself, read-only, and section 6 writes the appendix — so a
  session where `CDSW_APP_PORT` is already spoken for is no obstacle. The dashboard
  is a separate process either way, deployed as a Cloudera AI Application or run
  with `make dev`, and where it binds is decided in
  [`app/hosting.py`](app/hosting.py): every interface in a session, where the
  browser is outside the container, and loopback on a laptop, because this app has
  no authentication — see [Prerequisites](#prerequisites).
- **Egress.** A datacenter IP is the profile the public web engines block hardest, so
  the `ddgs` provider may return nothing from a session even though it works on a
  laptop. This is a measurement, not a defect: the preflight names which engines
  answered, and the notebook falls back to Wikipedia, OpenAlex, or arXiv — keyless
  APIs that do not block on IP reputation. See
  [`governance/model_cards/source-ledger-retrieval.md`](governance/model_cards/source-ledger-retrieval.md).

**As a library:**

```python
from source_ledger import text_to_urls

text_to_urls("Cloudera AI Workbench model deployment", max_results=10)
```

Options: `provider` (`"ddgs"` · `"wikipedia"` · `"openalex"` · `"arxiv"`),
`max_results`, `region`, `safesearch`, `timelimit`, and `backend`. URLs come back
deduplicated, in rank order. The support matrix is in
[`retrieval/README.md`](retrieval/README.md). To search *and record* in one call, use
[`data/record.py`](data/record.py) instead — `record.run("...", provider="arxiv")`
returns the URLs and writes the row.

## Architecture / Software Components

A synchronous request path that runs today, and a batch path written against the same
schema that has never been executed against a real cluster.

<img width="1053" height="523" alt="image" src="https://github.com/user-attachments/assets/dd919c8d-7771-4c97-92ee-c7dc2aaed3c9" />


| Layer | Component | Cloudera service | State |
| --- | --- | --- | --- |
| Serve | FastAPI + Jinja2 dashboard, server-rendered | Cloudera AI Application | runs locally |
| AI | `text_to_urls()` metasearch over four corpora | Cloudera AI Workbench | runs locally |
| Ingest | SQLite → Iceberg loader | Cloudera Data Engineering | never executed |
| Lakehouse | `raw_searches`, `raw_search_urls`, `curated_urls` | Iceberg on CDW | DDL never applied |
| Process | URL normalisation and enrichment (Spark) | Cloudera Data Engineering | never executed |
| Governance | Ranger policies, Atlas lineage, model card | SDX | never imported |

**Dependencies and security review scope.** Runtime dependencies are FastAPI, Uvicorn,
Jinja2, and the `ddgs` metasearch library; everything else is the Python standard
library, and there is no front-end build chain to audit. Outbound traffic goes to four
public search engines plus the Wikipedia, OpenAlex, and arXiv APIs — search endpoints
only, never the result URLs themselves. Data at rest is a single SQLite file holding
queries and links, never page content. Inbound, the Serve layer binds to localhost and
carries no authentication today; read [Prerequisites](#prerequisites) before exposing
it. `provision`, `deploy` and `govern` print what they *would* do and change nothing
without an explicit `--execute`; the two Spark jobs (`make ingest`, `make pipelines`)
have no such guard and submit work against whatever catalog they are pointed at.
Design decisions and the request path in full:
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Target Audience

- **Solution architects** evaluating the Cloudera Blueprint standard — *no coding
  required to assess it*; the repository is deliberately small enough to read in an
  afternoon, and every directory carries a README explaining what belongs there.
- **Data and ML engineers** who need a small, complete repository to fork — *comfortable
  Python and SQL*; `make new VERTICAL=healthcare USECASE=readmission-risk` clones the
  layout into a fresh blueprint. Extending storage to Iceberg additionally needs Spark
  and CDP familiarity.
- **Researchers and analysts** who need discovery captured, not just performed —
  *no programming at all*; the dashboard is the entire interface, and the resulting
  table is the deliverable. If you would rather work in a notebook,
  [`quickstart.ipynb`](quickstart.ipynb) runs top to bottom without an edit, and
  `python scripts/cli.py export --format csv` gets the record out as an appendix.

## Repository Structure

| Path | Description |
| --- | --- |
| `app/` | FastAPI + Jinja2 dashboard (Serve layer) |
| `retrieval/` | Search providers and the eval notebook (AI layer) |
| `data/` | SQLite dev store, schema, Iceberg DDL, and the loader |
| `pipelines/` | Cloudera Data Engineering (Spark) enrichment jobs |
| `infra/` | Deployment configs — CDP CLI provisioning and Terraform |
| `.cicd/` | Deployment scripts and the GitLab pipeline definition |
| `governance/` | SDX policies, data classification, model card |
| `docs/` | Extended documentation — architecture, business case, gates, worked example |
| `tests/` | Unit, data-quality, and retrieval-eval tiers |
| `scripts/` | `cli.py` terminal interface, `doctor.py` preflight, `kernel.py` notebook installs, `new-accelerator.sh` scaffold |
| `.github/` · `.gitlab/` | GitHub Actions, issue and merge-request templates |
| `quickstart.ipynb` | Run All: preflight, one recorded search, the record in HTML and in SQL, and the export |
| `requirements-notebook.txt` | Jupyter, kept out of `make install` |
| `METADATA.yaml` | Catalog metadata for the Cloudera blueprint website |
| `Makefile` | `make help` lists every target |

## Prerequisites

- **Python 3.11** (the version CI runs) and `git`.
- **bash** for the `make` targets — Git Bash or WSL on Windows, or the direct commands
  above.
- **Outbound internet.** Searches call public engine pages and the Wikipedia, OpenAlex,
  and arXiv APIs. Behind a proxy, set `DDGS_PROXY` or `HTTPS_PROXY`.
- **No API keys, accounts, or credentials of any kind** for everything that runs today.
- **CDP entitlement and the `cdp` CLI / Terraform** only for the platform layers, none
  of which has been executed.

**Before serving this anywhere but `127.0.0.1`:** there is no authentication and no CSRF
protection — `/clear`, `/delete/{id}`, `/dedupe`, and `/store` act on an unauthenticated
POST — and `ingress_cidrs` defaults to `0.0.0.0/0`. Note also that query text leaves the
environment: searches go to third-party public endpoints with no API key and therefore
no data-processing agreement, which must be raised with any customer whose data cannot
leave. See [`governance/DATA_CLASSIFICATION.md`](governance/DATA_CLASSIFICATION.md).

## Hardware Requirements

| Deployment | Minimum |
| --- | --- |
| Demo (everything that runs today) | 2 vCPU, 4 GB RAM, <1 GB disk |
| Production / enterprise (target, never provisioned) | CDP `LIGHT_DUTY` Data Lake; AI Workbench on `m5.xlarge`; CDE workers on `m5.2xlarge`, autoscaling 0–4. No GPU — there is no model here. |

Defaults live in [`infra/terraform/variables.tf`](infra/terraform/variables.tf). Size up
from measured load.

## Documentation

- [`docs/EXAMPLE.md`](docs/EXAMPLE.md) — one search across all five layers, ~20 minutes
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the stack, the request path, the
  decisions worth defending
- [`docs/BUSINESS_CASE.md`](docs/BUSINESS_CASE.md) — problem, Cloudera fit, scorecard
- [`docs/GATES.md`](docs/GATES.md) — what "done" means at each of the six phases
- [`governance/DATA_CLASSIFICATION.md`](governance/DATA_CLASSIFICATION.md) — retention
  and third-party disclosure
- [`governance/model_cards/source-ledger-retrieval.md`](governance/model_cards/source-ledger-retrieval.md)
  — intended use, out of scope, known limitations
- Every directory carries its own `README.md` explaining what goes there and which
  Cloudera tool automates it.

## Next steps after securing the URL table 
1. Sentiment analysis
2. Topic modeling
3. Knowledge graph construction
4. Summarization and multi-document summarization
5. AI-generated material detection
6. RAG system for the grounding
7. Media monitoring

## License

This project is licensed under the [Apache License 2.0](LICENSE).

## Disclaimer

*This blueprint is intended for Proof-of-Concept and research use only. It is not designed for production deployment. Use in production environments is at the user's own risk. The authors and contributors accept no liability for operational impacts or damages.*
