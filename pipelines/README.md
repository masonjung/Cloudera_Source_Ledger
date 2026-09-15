# `pipelines/` — Process layer

Batch jobs that turn raw search output into something worth querying. Everything
here reads Iceberg and writes Iceberg; nothing here talks to a search engine or
serves a request.

## What's here

| Path | What it is |
|---|---|
| `jobs/url_enrichment.py` | Normalises, deduplicates, and enriches raw URLs into `source_ledger.curated_urls` |
| `cde/url_enrichment.job.yaml` | Cloudera Data Engineering job definition — schedule, sizing, Spark conf |

## The job

`url_enrichment.py` is the lakehouse form of `db.dedupe_urls()`. Locally, dedupe is
a single `DELETE … WHERE id NOT IN (SELECT MIN(id) … GROUP BY url)`. At platform
scale the same intent becomes:

```
raw_search_urls  →  normalize_url()  →  group by url  →  MERGE INTO curated_urls
```

with three differences that matter:

1. **It normalises before comparing.** `https://WWW.Example.com/docs/?utm_source=x#intro`
   and `https://example.com/docs` are the same page. Raw string equality — what
   SQLite does — keeps both. The job lowercases the host, strips `www.`, drops
   [tracking parameters](jobs/url_enrichment.py), removes the fragment, and trims a
   trailing slash.
2. **It aggregates instead of deleting.** Duplicates are signal: a URL returned by
   six different searches is more interesting than one returned once. `times_seen`,
   `first_seen`, `last_seen`, and `best_position` retain what the local delete
   throws away.
3. **It is idempotent.** The `MERGE … ON target.url = source.url` means a rerun over
   the same window converges rather than double-counting. Rerunning is always safe.

## Run it

```bash
make pipelines                                              # full rebuild (needs Spark)
python pipelines/jobs/url_enrichment.py                     # the same thing directly
python pipelines/jobs/url_enrichment.py \
    --since 2026-08-01T00:00:00+00:00                       # incremental window
```

`MERGE_SQL` in [`jobs/url_enrichment.py`](jobs/url_enrichment.py) is the literal
statement that runs, written as SQL rather than a DataFrame write so it can be read
before it is scheduled. Read it there: there is no preview mode, and invoking this
job submits Spark work against whatever catalog `--catalog` and `--database` name.

## Conventions

- **Pure functions stay Spark-free.** `normalize_url()` and `url_parts()` are plain
  Python, unit-tested in [`tests/data_quality/`](../tests/data_quality/) with no
  cluster involved, then shipped to the executors as UDFs. Same code, both paths —
  a test that passes locally is testing what actually runs.
- **Idempotent or it does not ship.** Every job here must be safe to rerun over an
  overlapping window. Append-only aggregation is a bug waiting for a retry.
- **Explicit backfills.** `catchup: false` in the CDE spec. A missed window is rerun
  by hand with `--since` so the operator sees the range being reprocessed.
- **Raw is immutable.** Jobs read `raw_*` and write `curated_*`. Never edit raw in
  place — retrieval behaviour has to stay auditable against what the engines
  actually returned.
- **Size from measurement.** The executor sizing in the CDE spec is small on
  purpose. Raise it when you see spill, not before.

## Which Cloudera tool automates it

**Cloudera Data Engineering** (Spark on Kubernetes). `cde/url_enrichment.job.yaml`
is the full job definition — resource, schedule, sizing, Iceberg Spark conf.
`.cicd/deploy.sh` uploads the job file and re-imports the definition on every merge
to `main`, so the scheduled job always matches the committed code.

Airflow is available in CDE for multi-step DAGs. Source Ledger has one job, so a cron
schedule is the honest choice — reach for a DAG when ingest and enrichment need
real ordering guarantees between them.
