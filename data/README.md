# `data/` — Ingest + Lakehouse layers

Where search results land and where they live. Two storage tiers, same shape:

| Tier | Store | Used by |
|---|---|---|
| **Local / dev** | SQLite, one file at `data/source_ledger.db` | `app/server.py` via `db.py` — the default when you `make dev` |
| **Platform** | Apache Iceberg tables in the CDP lakehouse | `pipelines/`, BI tools, anything past a single node |

SQLite is not a placeholder for Iceberg — it is the demo path. An accelerator has to
run on a laptop during Discover, before any CDP environment exists. `ingest/load_to_iceberg.py`
is the bridge between the two.

## What's here

| Path | What it is |
|---|---|
| `db.py` | SQLite persistence. **All application SQL lives here** — no other module writes SQL against the dev store. |
| `record.py` | **The one writer.** Turns a search into a governed row; every interface goes through it. |
| `schema.sql` | The SQLite schema: `searches`, `search_urls`, one index |
| `iceberg/ddl.sql` | Iceberg DDL for the platform tier: `raw_searches`, `raw_search_urls`, `curated_urls` |
| `ingest/load_to_iceberg.py` | Loads the SQLite dev store into the raw Iceberg tables |
| `backup.py` | Dated local snapshots of the dev store, safe to take while it runs |
| `present.py` | Reading the record back for a human: read-only queries, the HTML table, and the CSV export — NULL kept distinct from unset in both |
| `source_ledger.db` | The database itself — **gitignored**, created on first run |
| `exports/` | Deliverables `present.export()` writes, `review-appendix.csv` among them — **gitignored**, created on first export |

## One writer

`db.save_search()` documents a contract its callers have to keep: an option a
provider does not apply is stored `NULL`, an option it applies but nobody set is
stored `""`, and collapsing the two would record a filter that never ran. That is
the difference between a search record and a search log.

A contract three interfaces each re-implement is a contract until one of them
drifts, so none of them call `db.save_search()` directly — the dashboard, `scripts/cli.py`,
and `quickstart.ipynb` all go through `record.save()`, which derives the NULL columns
from `source_ledger.supports()` rather than from anything a caller passed. `record.py` is
the only module in `data/` that imports from `retrieval/`, and that is why: the rule
cannot be enforced without knowing which options were actually applied.

## One export

The same argument, one layer out. An export is not a dump: it flattens the record to
one row per URL, keeps `position` 0-based as stored, and spells an unsupported option
`NULL` because CSV has one empty cell and this record needs two meanings for it. Those
are rules about the record, so `present.export()` owns them and `scripts/cli.py export`
and section 6 of `quickstart.ipynb` both run it. It started in `cli.py`; a second copy
in the notebook is what moved it here.

`present.py` writes a deliverable and never the store — its connections are `mode=ro`,
so it cannot become a second writer even by accident.

## The model

One search produces N URLs. That parent/child shape is identical in both tiers:

```
searches / raw_searches          one row per query, plus the options that produced it
   └── search_urls / raw_search_urls    one row per result URL, with its rank position
           └── curated_urls             pipelines/ output: deduplicated + domain-enriched
```

`created_at` is stored as an **ISO-8601 UTC string** (`datetime.now(timezone.utc).isoformat()`).
The Serve layer converts to local time for display only. Do not store local time.

## Run it

```bash
make ingest                                    # real load, needs Spark + Iceberg catalog
python data/ingest/load_to_iceberg.py          # the same thing directly
```

Move the dev database anywhere with the `SOURCE_LEDGER_DB` environment variable:

```bash
SOURCE_LEDGER_DB=/tmp/scratch.db make dev
```

## Backups

The dev store is gitignored and lives at one path, so without a second copy every
search you have run exists on exactly one disk.

```bash
make backup                          # -> backups/source-ledger-<utc>.db
make backup BACKUP_DIR=/d/archive    # anywhere else on this device
python data/backup.py --dest x.db    # an exact filename
```

Or press **Store** in the app's `saved_searches` header, which posts to `/store` and
calls the same `backup.snapshot()` the CLI does — so a snapshot taken from the button
and one taken from the shell are the same artifact in the same place. Set
`SOURCE_LEDGER_BACKUP_DIR` to point the button somewhere else, the way `SOURCE_LEDGER_DB` moves the
store itself.

Safe to run while `make dev` is serving. `db.backup()` uses SQLite's online
backup API rather than a file copy, because the app holds the database open:
copying the file mid-write can capture a torn page, and that corruption stays
invisible until the backup is the only copy left.

Snapshots are **never overwritten** — each run writes a new UTC-stamped name, and
an existing destination is an error rather than a replacement. `backups/` is
gitignored for the same reason `source_ledger.db` is: it holds the same real query text.

To restore, stop the server and copy a snapshot back over `data/source_ledger.db` — or
leave it where it is and point the app at it:

```bash
SOURCE_LEDGER_DB=backups/source-ledger-20260811-223521.db make dev
```

A backup is a *local* copy on this device, which is a different guarantee from
`make ingest`: that promotes the same rows into the governed Iceberg tables,
where retention and lineage apply. Neither replaces the other.

## Conventions

- **Only links are stored, never page content.** Source Ledger records URLs and the query
  that produced them. It does not fetch, cache, or persist the pages themselves.
  This is a governance commitment, not an implementation detail — see
  [`governance/DATA_CLASSIFICATION.md`](../governance/DATA_CLASSIFICATION.md).
- **`db.py` owns every statement that writes.** If SQL appears in `app/` or
  `retrieval/`, it is in the wrong file. `present.py` is the one other module with SQL
  in it and cannot become a second writer by accident: it connects through a
  `mode=ro` URI, so an UPDATE that reaches it raises.
- **Deletes cascade.** `search_urls.search_id` is a `REFERENCES … ON DELETE CASCADE`
  foreign key and `db.py` enables `PRAGMA foreign_keys = ON` on every connection —
  SQLite ignores the constraint otherwise. Keep that pragma when adding connections.
- **Additive migrations only.** `init_db()` runs `schema.sql` (all `IF NOT EXISTS`)
  then `ALTER TABLE … ADD COLUMN` for anything missing, so an existing database
  upgrades in place. Add new columns to *both* `schema.sql` and that migration list.
- **Iceberg schema changes go through the DDL file**, never an ad-hoc `ALTER` from a
  notebook. Iceberg schema evolution is tracked in table metadata; keeping the DDL
  authoritative is what makes the lineage in Atlas readable.

## Which Cloudera tool automates it

- **Ingest** — Cloudera **DataFlow** (NiFi) for continuous collection, or
  `ingest/load_to_iceberg.py` submitted as a **Cloudera Data Engineering** job for
  scheduled batch loads. Source Ledger's volume is small enough that batch is the honest
  default; DataFlow earns its place when searches arrive from a live feed rather
  than a form.
- **Lakehouse** — Iceberg tables on **CDW** / **CDP Data Lake** storage, created by
  `iceberg/ddl.sql`. Governed by SDX: policies in
  [`governance/sdx/`](../governance/sdx/), lineage captured automatically in Atlas.
