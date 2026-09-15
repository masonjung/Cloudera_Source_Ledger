## What this changes

<!-- One or two sentences. What is different after this merges? -->

## Why

<!-- The problem, not the solution. Link the issue if there is one. -->

Closes #

## Layers touched

<!-- Tick every directory this changes. Reviewers use this to know whose eyes
     are needed — a governance/ tick means the accelerator owner reviews too. -->

- [ ] `retrieval/` — retrieval
- [ ] `app/` — Serve layer
- [ ] `data/` — schema, ingest, Iceberg DDL
- [ ] `pipelines/` — Spark jobs
- [ ] `governance/` — policies, model cards, classification
- [ ] `infra/` — provisioning
- [ ] `tests/`
- [ ] `docs/`
- [ ] `.cicd/`

## Checks

- [ ] `make test` passes locally
- [ ] New behaviour has a test, or the reason it does not is stated below
- [ ] Any directory whose contract changed has its `README.md` updated in this MR

## Layer-specific

<!-- Delete the sections that do not apply. -->

**Changed a retrieval default in `retrieval/`?**
- [ ] `retrieval/notebooks/eval.ipynb` run, results dated in
      `governance/model_cards/source-ledger-retrieval.md`
- [ ] Defaults table in `retrieval/README.md` updated

**Changed a schema in `data/`?**
- [ ] Added to *both* `schema.sql` and the migration list in `db.init_db()`
- [ ] Iceberg DDL in `data/iceberg/ddl.sql` updated to match
- [ ] Existing databases upgrade in place — covered by a test

**Added a column that stores anything a user typed?**
- [ ] Classified in `governance/DATA_CLASSIFICATION.md` **in this MR**
- [ ] Ranger masking policy added if Confidential
- [ ] Retention considered and stated

**Changed a `pipelines/` job?**
- [ ] Still idempotent — safe to re-run over an overlapping window
- [ ] `MERGE_SQL` read in the diff; the statement that will run is what you intend
      (there is no dry run — `make pipelines` writes)

## Gate

<!-- Which phase does this advance? See docs/GATES.md. -->

Phase: <!-- Discover | Qualify | Architect | Build | Harden | Publish -->

- [ ] The gate criteria for that phase still hold after this change

## Anything a reviewer should know

<!-- Known gaps, follow-ups, decisions you made that could reasonably have gone
     the other way. Say it here rather than letting it be discovered. -->
