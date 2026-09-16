# Source Ledger — architecture

Natural-language text in, a governed table of URLs out. One capability, designed
across all five layers of the reference stack.

**What runs and what does not.** The Serve, AI, and local-storage layers execute
today. Everything platform-side — Iceberg, Spark, CDE, Ranger, and the CDP
provisioning under `infra/` — is designed and committed but **has never been
executed against a real environment**. The CDP provisioning and deploy scripts
dry-run and stop; the two Spark jobs do not — they write on invocation. This document
describes the intended architecture throughout, so read the state column below
before treating any of it as deployed. See "Planned platform integration" in
[`../README.md`](../README.md).

## The stack

```mermaid
flowchart TB
    subgraph GOV["Governed by SDX — Ranger, Atlas (governance/)"]
        direction TB
        subgraph SYNC["Synchronous request — runs today"]
            direction LR
            SERVE["Serve<br/>app/<br/>FastAPI + Jinja2"] --> AI["AI<br/>retrieval/<br/>text_to_urls()"] --> SQLITE[("SQLite tier<br/>data/db.py<br/>running today")]
        end

        subgraph BATCH["Batch path — designed, never run for real"]
            direction LR
            INGEST["Ingest<br/>data/ingest/<br/>SQLite to Iceberg loader"] --> ICEBERG[("Iceberg tier<br/>data/iceberg/<br/>DDL written, never applied")] --> PROCESS["Process<br/>pipelines/<br/>URL enrichment (MERGE)"]
        end

        SQLITE -. "scheduled job" .-> INGEST
    end

    classDef sync fill:#dbeafe,stroke:#1d4ed8,color:#1e3a5f;
    classDef batch fill:#f3f4f6,stroke:#6b7280,color:#374151;
    class SERVE,AI,SQLITE sync;
    class INGEST,ICEBERG,PROCESS batch;
```

## Layer by layer

| Layer | Directory | Component | Cloudera service | State |
|---|---|---|---|---|
| **Serve** | `app/` | FastAPI + Jinja2 dashboard | Cloudera AI Application | runs locally |
| **AI** | `retrieval/` | `text_to_urls()` — metasearch retrieval | Cloudera AI Workbench | runs locally |
| **Ingest** | `data/ingest/` | SQLite → Iceberg batch loader | Cloudera Data Engineering | never executed |
| **Lakehouse** | `data/iceberg/` | `raw_searches`, `raw_search_urls`, `curated_urls` | Iceberg on CDW / Data Lake | DDL never applied |
| **Process** | `pipelines/` | URL normalisation and enrichment | Cloudera Data Engineering | never executed |
| **Governance** | `governance/` | Ranger policies, model card, classification | SDX | never imported |

**On the AI layer's directory name.** The Forge standard calls this layer `ai/`, and
Source Ledger deliberately does not. There is no model here and no inference: the layer is
keyword retrieval — a dispatch table, four HTTP clients, and an XML parser, on one
dependency. A directory called `ai/` would claim a capability the code does not have,
which is the same failure the support matrix in `retrieval/providers.py` exists to
prevent one control at a time. The layer still fills the standard's AI slot and still
maps to Cloudera AI Workbench; only the directory is named for its contents. An
accelerator that does add a model should name the slot back.

## The request path

A search is synchronous and touches three layers:

1. **`POST /search`** — `app/server.py` hands the form to `record.normalize()`, which
   whitelists every option against `OPTIONS`, clamps `max_results` to 1–50, and joins
   the checked engines into a fallback chain. `scripts/cli.py` and `quickstart.ipynb`
   enter here instead of at the route, through the same function.
2. **Retrieval** — `source_ledger.text_to_urls()` dispatches to the selected provider,
   dropping any option that provider does not apply. `ddgs` queries web engines
   concurrently and pools whichever results come back first; the others call one
   API. Returns URLs deduplicated, in rank order.
3. **Persist** — `db.save_search()` writes the query, the provider, the options that
   were actually applied, and the URLs in one transaction. An option the provider
   does not support is stored `NULL`, distinct from `""` for one it supports and the
   search did not use.
4. **303 redirect** back to `/` with a flash message. Reloading never re-runs a
   search.

The batch path runs on a schedule and never blocks a user: ingest at 02:00, enrichment
at 02:30, both idempotent.

## Decisions worth defending

**SQLite is not a placeholder — it is the demo path.** An accelerator has to run on
a laptop during Discover, before any CDP environment exists. The two tiers share a
schema shape, and `data/ingest/load_to_iceberg.py` is the bridge. What would be
wrong is *pretending* SQLite is the production store; the split is explicit.

**No JavaScript beyond a submit indicator.** The whole accelerator installs with
`make install` and runs with one command. That keeps the
Discover → Qualify demo loop short, which is worth more here than a richer UI. The
cost is real — no incremental updates, a full page render per action — and the
swap path is documented in [`app/README.md`](../app/README.md).

The rule is *no build step and no framework*, not zero `<script>` tags, and one
exception earns its place. A search is a synchronous form post that blocks for up
to 12s on `ddgs` and ~38s on an arXiv stall, and the page had no way to say so:
the button stayed idle, the table stayed still, and the only feedback was the
browser's tab spinner — invisible on a projector. Watching a demo audience decide
the app had hung is the failure this closes.

So `index.html` carries roughly forty lines of inline script that disable the
submit button, spin it, and count elapsed seconds past three. It is progressive
enhancement in the strict sense: **with scripting off, the form posts exactly as
it did before and none of it runs**, which is why the test suite — which executes
no JavaScript — still covers the whole Serve layer unchanged. No dependency, no
bundler, no `package.json`.

What it deliberately is *not* is real progress. Per-engine status ("querying
yahoo…") is not available to a synchronous form post; it would need the search
moved to a background task plus a status endpoint to poll, which is a different
architecture and wants an ADR. The indicator says *still working, this long* —
which is the honest claim, and the one that stops a viewer concluding the app
died.

**Multi-engine is resilience, and its mechanics are ddgs's, not ours.** Selecting
several engines makes a throttled one survivable rather than fatal — that part holds,
and it is the reason to select more than one.

This document previously said ddgs "tries engines in order and stops once it has
`max_results`," with order load-bearing. **That is not what ddgs 9.x does**, and the
correction matters because it changes what the `backend` column can be claimed to
mean. Reading `ddgs/ddgs.py` and probing the live library:

- Engines are submitted to a `ThreadPoolExecutor` **concurrently**, not tried in
  sequence. `max_workers = min(unique_engines, ceil(max_results / 10) + 1)`, so at
  `max_results=10` two engines are queried at once.
- Results from whichever engines return are pooled in a `ResultsAggregator` and
  re-ranked together by ddgs's own `SimpleFilterRanker`. Order of selection is a
  weak input to that, not a priority chain.
- Engines that do not finish inside the first `wait()` appear to have their results
  dropped. Observed: `yahoo` alone returned 7 URLs, `startpage` alone returned 10,
  and `yahoo,startpage` returned **7** — the chain neither filled to `max_results`
  nor merged in what startpage had.

Two consequences the rest of this repo depends on. First, **`backend` records the
engines *asked*, not the engine that answered** — see the note under *Providers are
an axis*. Second, selecting more engines is not free extra coverage; measure it with
`retrieval/notebooks/eval.ipynb` rather than assuming.

**The worker formula meant half the selection was never queried.** Following the
arithmetic above to its conclusion: `ceil(10 / 10) + 1 == 2`. At the UI default of
`max_results=10`, checking all four engines queried **two** of them. The resilience
the four checkboxes advertise was half delivered, and the shortfall was invisible —
a search that quietly asked fewer engines looks exactly like one that asked them all.

`_search_ddgs` now asks ddgs for `max(max_results, 30)`, which sizes the pool to four
workers, and `text_to_urls` re-applies the caller's ceiling as it always has. The
widened request cannot widen what a caller receives; it only widens what is asked.
This reaches into a library internal, so **re-check it on a ddgs upgrade** — if the
formula changes, the repo silently reverts to querying too few engines.

**A blocked search and an empty one are not the same claim.** ddgs reports "every
engine failed" as an empty result, which reached the user as a calm *No results
found.* — a dead network wearing the face of a corpus with nothing in it. That is
indefensible in a repo whose whole argument is that a search is a record.

Three cases now separate, in descending order of evidence:

- **An engine said why.** ddgs logs `Error in engine %s: %r` at INFO and then discards
  the exception; `source_ledger` listens for those records and raises `EngineError`
  carrying every `(engine, reason)` pair, which the Serve layer renders by name.
- **No engine answered in time.** Engines that miss the `wait()` land in `not_done`
  and are dropped with no exception and no log — the slow-network failure, and the
  one ddgs helps with least. The only evidence left is the clock: an empty search
  that consumed the whole collection window ran out of time rather than came up
  empty.
- **A genuine miss** still returns `[]` and still reads as *No results found.*

One gap stays open and is documented rather than papered over: an engine answering
HTTP 200 with an empty body or a challenge page raises nothing and logs nothing, so
it remains indistinguishable from a real miss. Rate limiting frequently looks exactly
like that. `make doctor` is the answer to that gap — it probes each engine
individually and treats an instant empty answer as a probable block, which is a
heuristic it states as one.

**Providers are an axis, not links in the chain.** The decision above is why
Wikipedia, OpenAlex, and arXiv are a separate choice rather than four more entries
in `backend`. Fall-through assumes the engines are substitutes for one another;
these are different corpora, so a web-search miss falling through to arXiv would
answer a product question with physics preprints and look like a successful search.
Near-zero overlap is exactly what makes them worth adding *and* what disqualifies
them as fallbacks. One search therefore uses one provider, recorded on the row.

Merging results across providers is a different feature, not a smaller version of
this one: it needs a defensible meaning for `max_results` across corpora, and
concurrency to stay inside the 20 s budget the synchronous form post allows. It
would supersede this decision, so it needs an ADR rather than a patch.

**Provenance resolves to the corpus, not the engine.** `raw_search_urls.provider`
records which corpus returned each URL, and `curated_urls.providers` accumulates
every corpus that returned it — a URL with more than one entry was found
independently, which is a stronger signal than the same URL twice from one engine.

There is deliberately **no per-URL engine column**. ddgs pools results from several
engines into one list and discards which engine produced each row, so any such value
would be invented rather than recorded. Getting it truthfully would mean querying
each engine separately and attributing the results here — replacing ddgs's
aggregation and ranking with ours, at N× the latency. That is the same merge decision
above, and it needs the same ADR.

**A provider only advertises options it applies.** The support matrix in
`retrieval/providers.py` is declared once and drives four things: which kwargs reach the
provider, which columns are written versus left `NULL`, which controls the template
renders, and the CSS that hides the rest. Restating it anywhere would let the copies
drift, and the symptom would be a control that silently does nothing. With no
JavaScript a hidden control still posts its value, so the CSS is presentation only
and `app/server.py` remains the enforcement point.

**The Process layer normalises; the local dedupe does not.** `db.dedupe_urls()`
compares raw strings. The Spark job strips `www.`, tracking parameters, and
fragments first, then aggregates rather than deletes — duplicates are signal
(`times_seen`), not noise. The divergence is deliberate: the job is strictly more
aggressive, never less, and
[`tests/data_quality/`](../tests/data_quality/test_url_normalization.py) asserts it.

**Links only, never page content.** Enforced structurally: no HTTP client in this
repo fetches the content of a result URL. Retrieval reads the URL out of each result
and discards the rest — including the snippets and abstracts the API providers
return. `retrieval/providers.py` has exactly one outbound seam, `_get_bytes`, and it calls
search and metadata APIs only. Pointing it at a result URL is a classification
change, not a feature — see
[`governance/DATA_CLASSIFICATION.md`](../governance/DATA_CLASSIFICATION.md).

That single seam is also what makes the rule testable: `tests/conftest.py` severs it
for the whole suite, so "no network in the default run" is enforced rather than
remembered.

## Where the layering is tested

The Serve layer holds no SQL and no retrieval logic; `data/db.py` holds every
statement; `retrieval/source_ledger.py` has no database and no HTTP handling. The check on that
claim is concrete: swapping the front-end for React should require no change in
`retrieval/`, `data/`, `pipelines/`, or `governance/`.

## Known constraints

- **Query text leaves the environment.** Searches go to third-party public endpoints
  with no API key and therefore no data-processing agreement. This is a deployability
  constraint, not a footnote — raise it first with any customer whose data cannot
  leave. See
  [`governance/DATA_CLASSIFICATION.md`](../governance/DATA_CLASSIFICATION.md#third-party-disclosure).
- **Retrieval is not reproducible.** Engines re-rank continuously; the same query
  tomorrow returns different URLs. Every search persists its full option set and
  timestamp, so a result set is *explainable* even when it is not repeatable.
- **Absence is not evidence.** Results are what a public engine ranked in the top N.
  A missing URL does not mean the page does not exist. Never use Source Ledger output to
  conclude a document does not exist.
- **SQLite serialises writes.** Fine for a demo, wrong for concurrent users. The
  Iceberg tier is the answer, and the ingest bridge is where that transition happens.

## Scale

Source Ledger's volume is small — a search is a form post, not a stream. The sizing across
`infra/` and `pipelines/cde/` reflects that deliberately: CDE scales to zero between
runs, executors are modest, enrichment is daily. **Raise these from measured spill,
not from optimism.** The architecture that would change first if volume grew is
ingest — DataFlow earns its place when searches arrive from a live feed rather than
a form.
