# `tests/` — the Harden gate

Nothing ships until this directory is green. `make test` is what the Harden gate in
[`docs/GATES.md`](../docs/GATES.md) means in practice.

## What's here

| Path | Layer under test | What it covers |
|---|---|---|
| `test_urlvestigia.py` | AI | Retrieval contract: rank order, dedupe, the `href`/`url` key rename, option forwarding, error propagation |
| `test_providers.py` | AI | The support matrix, per-provider URL extraction, and the outbound requests — asserted on the request, not just the results |
| `test_db.py` | Ingest | Round trips, cascade deletes, dedupe semantics, in-place migration, SQL parameterisation |
| `test_record.py` | Ingest | The one writer: whitelisting, clamping, and the NULL rule, parametrized from the live support matrix |
| `test_ingest.py` | Ingest | The SQLite → Iceberg bridge: what `read_sqlite` returns and that its column lists match the SELECTs |
| `test_server.py` | Serve | Input whitelisting, clamping, POST-redirect-GET, XSS escaping, table rendering |
| `test_hosting.py` | Serve | Where the app binds and which URL a reader is given — loopback on a laptop, the proxied port in a Cloudera AI session |
| `test_cli.py` | Serve | The terminal interface: the same guarantees, plus exit codes, the stdout/stderr split, export grain, and ASCII output |
| `test_quickstart.py` | Serve | `quickstart.ipynb` — the bootstrap resolving the repo from any working directory, and both notebooks committed without outputs |
| `data_quality/test_url_normalization.py` | Process | URL normalisation — idempotence, what must collapse and what must stay distinct |
| `data_quality/test_enrichment_sql.py` | Process | That `STAGE_SQL` and `curated_urls` stay column-aligned, since `INSERT *` binds positionally |
| `eval/test_retrieval_eval.py` | AI | Eval harness: the retrieval contract, plus an opt-in live tier |
| `conftest.py` | — | Path setup, temp database, stubbed retrieval client, the network guard |

## Run them

```bash
make test                     # everything except the live tier
pytest tests -q               # same thing
pytest tests/eval --live      # add the tests that call real search engines
pytest tests -q -k dedupe     # one behaviour
```

Install with `pip install -r app/requirements.txt -r tests/requirements.txt`, or
just `make install`.

Current state: **346 passing, 13 skipped** (the live tier).

## The two tiers

**Contract tests always run.** They stub the search engine, so they are fast,
deterministic, and safe in CI.

**Live tests need `--live`.** They call DuckDuckGo, Yahoo, Startpage, and Yandex for
real, plus the Wikipedia, OpenAlex, and arXiv APIs. They are off by default because
*CI must never fail because a provider is rate-limiting* — a red pipeline has to mean
"we broke something," or people stop reading it.

Everything else is severed from the network structurally, not by convention: the
`no_network` fixture in `conftest.py` breaks `providers._get_bytes`, the single seam
every provider goes through. A provider that reached out some other way would escape
the stub and start making real calls in CI, and the symptom would be a slow flaky
suite rather than a readable failure — so the guard has its own test.

Run the live tier by hand before a release, and record what it reports in
[`governance/model_cards/urlvestigia-retrieval.md`](../governance/model_cards/urlvestigia-retrieval.md).
A live failure is often a measurement rather than a defect; the per-engine
availability test `skip`s instead of failing when an engine returns nothing, because
that outcome is expected and worth recording rather than worth blocking on.

## Conventions

- **No network in the default run.** `conftest.client` stubs `text_to_urls`, and
  every AI-layer test installs a fake `DDGS`. If a test needs the network, mark it
  `@pytest.mark.live`.
- **No test touches the real database.** `conftest.py` redirects `URLVESTIGIA_DB` to a
  temp file *at import time*, before `db` is ever imported — `db.DB_PATH` is
  resolved once at module load, so a fixture would be too late.
- **Test the guarantee, not the implementation.** `assert_retrieval_contract()` in
  `eval/` states every promise `text_to_urls` makes, and both tiers call it. Live
  and stubbed runs check identical invariants.
- **Pure functions get tested without a cluster.** `normalize_url()` and
  `url_parts()` are plain Python that Spark ships as UDFs, so the code these tests
  exercise is the code that runs in production.
- **A named bug gets a named test.** `test_accepts_legacy_url_key` exists because
  ddgs renamed a result key; `test_migrates_a_pre_options_database` exists because
  the schema gained columns after databases already existed in the wild.

## What is not covered

Stated plainly, because an unstated gap reads as a claim:

- **No Spark integration test.** `url_enrichment.py`'s pure functions are covered,
  and `data_quality/test_enrichment_sql.py` checks the staged columns still line up
  with the Iceberg DDL — but that is string analysis, not execution. The `MERGE` and
  the Iceberg writes need a cluster. Verify with `--execute` against a dev
  environment before promoting a pipeline change.
- **No test of the Spark write in `load_to_iceberg.py`.** `read_sqlite` and the
  column lists are covered; `createDataFrame` and `writeTo` are not.
- **No Terraform or CDP CLI test.** `make provision` dry-runs, which is the check.
- **No browser test.** The UI has no JavaScript, so rendering is asserted against
  the returned HTML in `test_server.py`.
- **No load or concurrency test.** SQLite serialises writes; a multi-user
  deployment on Iceberg would need its own harness.

## Which Cloudera tool automates it

`.cicd/pipeline.yml` runs `pytest tests -q` on every merge request, and
`.github/workflows/ci.yml` runs it on every push — the gate is enforced by CI, not by
habit. Neither passes `--live`. For the platform tiers, data quality checks that need real
tables belong in a **Cloudera Data Engineering** job alongside the pipelines, so they
run against the lakehouse on the same schedule as the data.
