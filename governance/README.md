# `governance/` — Security, policy, and lineage

The layer that decides whether this accelerator can be deployed at a customer.
Everything here is SDX-facing: who may read what, what is recorded about the AI
component, and how the data is classified.

## What's here

| Path | What it is |
|---|---|
| `DATA_CLASSIFICATION.md` | Field-by-field classification, third-party disclosure, retention. **Read this first.** |
| `model_cards/source-ledger-retrieval.md` | Model card for the retrieval component — intended use, limitations, evaluation |
| `sdx/ranger-policies.json` | Ranger access, masking, and row-filter policies, plus Atlas classifications |

## The shape of the problem

Source Ledger has exactly **one sensitive column**: `raw_searches.query`, the free text a
user typed. Everything else is public web addresses or configuration.

That single fact drives the whole policy set. Analysts get the URL tables outright;
they see `query` only as a hash. Engineers see everything. The app's service account
can append to raw and nothing else. Narrow, and defensible in a review — which is the
point.

The second fact worth stating plainly: **query text leaves the customer's
environment**, because searches go to third-party public endpoints with no API key
and therefore no data-processing agreement. That is a deployability constraint, not
a footnote. It is the first thing to raise with a customer whose data cannot leave.

## Apply the policies

```bash
make govern                 # dry-run: prints what would be imported
.cicd/deploy.sh govern      # real import into Ranger
```

`${CDP_ENV}` and the group names are placeholders — repoint them to the customer's
IdP groups before importing. The `source_ledger_analysts` / `source_ledger_data_engineers` names are
illustrative, not real.

## Conventions

- **Classify before you store.** A new column gets a row in `DATA_CLASSIFICATION.md`
  in the same merge request that adds it. Not afterwards.
- **Tag at creation, not by backfill.** Atlas classifications are attached when the
  table is created so lineage and tag-based policies work from the first load.
- **Every AI component gets its own card.** If retrieval grows an LLM reranker or an
  agent loop, that component needs a new file in `model_cards/` — do not extend the
  existing card to cover a different mechanism.
- **Undated evaluation is not evidence.** Engine behaviour drifts. A measurement in
  a model card without a date, or older than a quarter, should be re-run before it
  is relied on.
- **Deny by default.** No policy grants `public`. Adding a group is a deliberate act
  with a name attached to it.

## Known gaps

Stated here rather than discovered at a customer review:

- **No row-level retention on `raw_searches`.** Iceberg snapshot expiry governs time
  travel, not the rows themselves. A deployment with a query-retention requirement
  needs a scheduled delete job in `pipelines/`.
- **No identity fields today**, which is what keeps the classification light. Adding
  `user_id` for multi-tenancy changes the posture materially — the checklist for
  that is in `DATA_CLASSIFICATION.md`.
- **The retrieval model card has no recorded evaluation.** Due at the Harden gate;
  run `retrieval/notebooks/eval.ipynb`.
- **The Serve layer has no authentication and no CSRF protection.** `/clear`,
  `/delete/{id}`, `/dedupe`, and `/store` act on an unauthenticated POST, and
  `/clear` is destructive. This is correct for a `127.0.0.1` demo and wrong the
  moment the app is hosted — `.cicd/deploy.sh app` publishes it as a Cloudera AI
  Application behind Knox, which authenticates the *user* but does not make the
  routes safe against a cross-site POST. Fix before the first hosted deployment.

## Which Cloudera tool automates it

**SDX** — the shared security and governance layer across CDP:

- **Ranger** enforces the access, masking, and row-filter policies in
  `sdx/ranger-policies.json`. Import is idempotent (`updateIfExists=true`), so
  `.cicd/deploy.sh` can re-apply on every merge.
- **Atlas** captures lineage automatically for the Spark paths and holds the
  classifications declared in the same file. The engine call itself is outside
  Atlas's view — provenance for "which engine produced this URL" comes from the
  `backend` column, so keep it populated.
- **Knox** fronts the Serve layer; the app authenticates through it rather than
  managing its own sessions.
