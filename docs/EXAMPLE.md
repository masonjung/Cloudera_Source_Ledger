# Walkthrough — Source Ledger across every layer

Follow one search from a form post to a governed row in `curated_urls`. Steps 0-3 and
6 run on a laptop. Steps 4 and 5 are the Spark jobs: they write on invocation and need
a cluster, so they are presented here as what they do rather than as something to run
locally.

Budget about 20 minutes.

---

## 0. Set up

```bash
pip install -r app/requirements.txt -r tests/requirements.txt
make test          # 255 passing, 13 skipped (the live tier)
```

If that is green, every layer imports and every contract holds. Start here — a
failure now saves you debugging the wrong thing in step 3.

---

## 1. The retrieval layer, on its own

The capability, with no server and no database:

```bash
python scripts/example.py
```

```python
# scripts/example.py, in full
from source_ledger import text_to_urls

for url in text_to_urls("Cloudera CDP supports use cases", max_results=16):
    print(url)
```

That is the whole retrieval layer's public surface. **This call hits a live search engine** —
if it returns nothing, an engine is throttling you, which is exactly the failure mode
the fallback chain exists for:

```python
text_to_urls("Cloudera CDP use cases", backend="duckduckgo,yahoo,startpage")
```

`ddgs` queries every listed engine concurrently and pools whichever results come back
first — the list is a resilience set, not a priority chain. →
[`retrieval/README.md`](../retrieval/README.md) ·
[`docs/ARCHITECTURE.md`](ARCHITECTURE.md#decisions-worth-defending)

---

## 2. The Serve layer

```bash
make dev        # → http://127.0.0.1:8000/
```

Type a question, pick engines, hit **Search**. What happens:

1. `POST /search` — every option is whitelisted against `OPTIONS` in
   [`data/record.py`](../data/record.py); `max_results` is clamped to 1–50.
   (`python scripts/cli.py search "..."` reaches the same code without the browser.)
2. Checked engines are filtered, deduplicated, and joined into a fallback chain.
   Order is preserved for a reproducible request record, not because engines are
   tried in sequence — see step 1.
3. `source_ledger.text_to_urls()` runs.
4. `db.save_search()` persists the query, its full option set, and the URLs.
5. **303 redirect** back to `/` with a flash message — reloading never re-runs the
   search.

Try a few things that should not work:

- Submit an empty box → nothing reaches a search engine.
- Ask for 9999 results → clamped to 50.
- Search `<script>alert(1)</script>` → rendered escaped, not executed.

All three are asserted in [`tests/test_server.py`](../tests/test_server.py).

---

## 3. The Lakehouse layer, local tier

The searches you just ran are in SQLite:

```bash
sqlite3 data/source_ledger.db "SELECT id, query, backend, region FROM searches ORDER BY id DESC LIMIT 5;"
sqlite3 data/source_ledger.db "SELECT position, url FROM search_urls WHERE search_id = 1 ORDER BY position;"
```

Two tables, parent and child, cascade on delete. Every statement the application runs
lives in [`data/db.py`](../data/db.py) — nowhere else.

Now run two searches likely to share a result, and press **Dedupe URLs** in the UI.
It keeps the earliest occurrence of each URL and removes any search left empty. That
is the local tier's answer to duplication; step 5 shows the lakehouse's better one.

---

## 4. Ingest — crossing to the platform tier

```bash
make ingest        # needs Spark + a live Iceberg catalog; writes on invocation
```

[`data/ingest/load_to_iceberg.py`](../data/ingest/load_to_iceberg.py) reads whatever
`app/server.py` has been writing and appends it to the raw tables:

| From SQLite | To Iceberg |
|---|---|
| `searches` | `spark_catalog.source_ledger.raw_searches` |
| `search_urls` | `spark_catalog.source_ledger.raw_search_urls` |

`--since` takes an ISO-8601 UTC watermark so a scheduled run loads only what is new;
omit it for a full load. `created_at` arrives as a string and is cast to a real
timestamp on the way in, and `ingested_at` is stamped by the job.

There is no preview mode — running this submits the write. What *is* checkable without
a cluster is the half that does not need one: `read_sqlite`, the watermark filter, and
the column lists that must stay aligned with
[`data/iceberg/ddl.sql`](../data/iceberg/ddl.sql) are all covered by

```bash
pytest tests/test_ingest.py -q
```

---

## 5. Process — where the interesting work is

```bash
make pipelines     # needs Spark + a live Iceberg catalog; writes on invocation
```

The statement it runs is `MERGE_SQL` in
[`pipelines/jobs/url_enrichment.py`](../pipelines/jobs/url_enrichment.py), written as
literal SQL rather than a DataFrame write so it can be read before it is scheduled.

The normalisation it applies first is pure Python and needs no cluster, so you can see
it on a laptop:

```bash
python -c "import sys; sys.path.insert(0, 'pipelines/jobs'); from url_enrichment import normalize_url; print(normalize_url('https://WWW.Example.com/Docs/?utm_source=news&topic=iceberg#intro'))"
```

```
https://example.com/Docs?topic=iceberg
```

**Compare this to step 3.** `db.dedupe_urls()` compares raw strings, so those two URLs
would both survive as separate rows. The Spark job strips `www.`, tracking parameters,
and the fragment first — and then *aggregates rather than deletes*:

| | Local dedupe | Enrichment job |
|---|---|---|
| Compares | raw strings | normalised URLs |
| Duplicates | deleted | counted (`times_seen`) |
| Keeps | earliest row | earliest **and** latest sighting, best rank, every `search_id` |
| Re-runnable | yes | yes — `MERGE` converges |

Duplicates are signal. A URL six different searches returned is more interesting than
one returned once, and the local tier throws that away.

The normalisation functions are plain Python, shipped to Spark as UDFs, and tested
without a cluster:

```bash
pytest tests/data_quality -q
```

---

## 6. Governance

Read [`governance/DATA_CLASSIFICATION.md`](../governance/DATA_CLASSIFICATION.md).
The shape of the whole policy set follows from one fact: **exactly one column is
sensitive** — `raw_searches.query`, free text a user typed. Everything else is public
web addresses or configuration.

So [`governance/sdx/ranger-policies.json`](../governance/sdx/ranger-policies.json)
stays narrow: analysts read the URL tables outright and see `query` only as a hash;
engineers see everything; the app's service account can append to raw and nothing
else. Narrow, and defensible in a review.

Then read [`governance/model_cards/source-ledger-retrieval.md`](../governance/model_cards/source-ledger-retrieval.md) —
in particular **what it must not be used for**. Source Ledger cannot tell you a document does
not exist. Absence from the results means an engine did not rank it in the top N.

Note what the model card does *not* have: a dated evaluation. That is a real gap, and
it is why the Harden gate is still open.

---

## 7. Deploy — the shape of it

```bash
make -n deploy     # print the make targets without running them
make deploy        # the deploy script's own dry run — every command, no changes
```

Three steps, in this order:

```
govern → jobs → app
```

Access control lands before data moves; data lands before anything serves it. A
deploy that publishes the app first shows a stakeholder an empty dashboard and a
policy gap simultaneously.

Provisioning is separate and runs **once** per environment:

```bash
make provision     # dry run — every CDP CLI call, printed
```

→ [`infra/README.md`](../infra/README.md), [`.cicd/README.md`](../.cicd/README.md)

---

## 8. Start your own

```bash
make new VERTICAL=healthcare USECASE=readmission-risk
```

Creates `../cloudera-forge-healthcare-readmission-risk/`: the layout and every
directory's `README.md` guidance, with Source Ledger's own code cleared out, re-pointed to
your name, and a fresh git history.

Then work directory by directory, using each `README.md` as the guide and
[`GATES.md`](GATES.md) as the bar for each handoff.

---

## What this example is meant to teach

| The pattern | Where you saw it |
|---|---|
| Two storage tiers, one schema shape — laptop *and* lakehouse | steps 3–4 |
| The Process layer does more than move data | step 5 |
| Classify before you store, and let it shape the policy set | step 6 |
| Pure functions test without a cluster; Spark ships the same code | step 5 |
| Dry run by default for provisioning and deploying | step 7 |
| A job that writes says so, and is read before it is scheduled | steps 4, 5 |
| Provisioning and deploying are different operations | step 7 |
| Stating known gaps beats having them found | step 6 |

The search logic is 40 lines. Everything else here is the accelerator.
