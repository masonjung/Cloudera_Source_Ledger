# Source Ledger — business case

> **Scope note.** Source Ledger's primary job is to be the Forge template's *worked example*
> — a thin but complete accelerator wired across every layer, small enough to read in
> an afternoon. The case below is written as a real one because a worked example that
> skips the business case teaches the wrong lesson. Figures marked _(estimate)_ are
> reasoned, not measured; replace them with customer numbers before this is used to
> justify anything.

## The problem

Research that starts with "find me the sources on X" is done in a browser and lost in
a browser. The tabs close, the links live in someone's history, and the next person
who needs the same sources starts from zero. Nothing about which query produced which
results is recoverable, so the search cannot be reviewed, repeated, or handed over.

This is invisible because it never fails loudly. It shows up as the same research
being redone, and as findings whose provenance nobody can reconstruct three weeks
later.

## What Source Ledger does

Turns a natural-language question into a persisted, governed table of source URLs —
with the query, the engines, the region, the time window, and the timestamp stored
alongside. A search becomes an artifact instead of an activity.

Concretely:

- **Discovery is captured**, not just performed. The result set survives the session.
- **Provenance is explicit.** Every URL carries the query and options that produced
  it, so a reviewer can see *how* a source was found.
- **Duplicates become signal.** A URL returned by six different searches is more
  interesting than one returned once — `curated_urls.times_seen` retains what a
  browser throws away.
- **It is governed.** Ranger masks the query text for analysts; Atlas carries lineage;
  the retrieval component has a model card stating what it must not be used for.

## Why Cloudera

The honest answer first: **the retrieval itself does not need a platform.** It is a
library call, and it runs fine on a laptop. If Source Ledger stopped at "get me some links,"
this would not be an accelerator and the right decision would be to say so.

What needs the platform is everything after retrieval:

| Need | Layer |
|---|---|
| Search history that outlives a session, at organisational scale | Iceberg lakehouse |
| Normalisation and deduplication across every search ever run | Data Engineering (Spark) |
| Query text is user-typed free text — it needs masking and access control | SDX / Ranger |
| An opaque third-party ranker driving decisions needs a governance record | Model card, Atlas |
| Multiple people, one governed store | CDW, not a laptop file |

That is the line worth drawing at any Qualify gate: the capability is not the
accelerator. The governed, queryable, reusable *record* of the capability is.

## Scorecard

Scored against the Qualify criteria. **≥ 4.0 / 5 advances.**

| Criterion | Weight | Score | Justification |
|---|---:|---:|---|
| Market demand | 25% | 3 | Real but not loud. Discovery-and-provenance is a recurring need across verticals rather than a named ask from a specific segment. |
| Revenue impact | 25% | 3 | Indirect. Source Ledger's value to Cloudera is as a teaching artifact and a starting point, not as a line item _(estimate)_. |
| Technical fit | 20% | 5 | Uses every layer of the reference stack as designed — Ingest, Lakehouse, Process, AI, Serve, all governed by SDX. |
| Reusability | 20% | 5 | The layout, the two-tier storage pattern, the eval harness, and the governance scaffolding transfer to any accelerator. The search logic is 40 lines. |
| Effort | 10% | 5 | Days, not quarters. No API keys, no accounts, no build step. |
| **Weighted total** | | **4.05** | Advances — on fit and reusability, not on demand. |

The shape of that score is the interesting part. Source Ledger advances because it is
*exemplary*, not because it is in demand. A candidate with this profile and no
teaching role would be a **hold**, and saying so is what makes the scorecard worth
filling in.

## Cost

- **To run:** effectively zero. No API keys, no accounts, no per-query cost — searches
  go to public endpoints. The platform cost is CDE scaling to zero between runs plus
  Iceberg storage measured in megabytes.
- **To build:** already built.
- **To adapt:** the reusable part is the structure. A team taking this as a starting
  point replaces `retrieval/` and keeps almost everything else.

## Risks

| Risk | Why it matters | Where it is handled |
|---|---|---|
| **Query text leaves the environment** | Public search endpoints, no API key, therefore no data-processing agreement. Disqualifying for some customers. | [`governance/DATA_CLASSIFICATION.md`](../governance/DATA_CLASSIFICATION.md#third-party-disclosure) |
| **Third-party dependency with no contract** | Engines add rate limits and blocks without notice; usage is bounded by their tolerance, not an SLA | Four-engine fallback chain; availability measured in `retrieval/notebooks/` |
| **Results are not reproducible** | Engines re-rank continuously | Full option set persisted per search — explainable, not repeatable |
| **Ranking mistaken for authority** | `best_position` is SEO, not credibility | Stated as out-of-scope in the model card |

## What success looks like

For Source Ledger as a worked example: a developer clones the template, reads
[`EXAMPLE.md`](EXAMPLE.md), and understands what belongs in each of the ten
directories well enough to build their own accelerator without asking. That is the
Publish gate's last checkbox, and it is the only measure that matters here.

For Source Ledger as a deployed tool: research questions asked more than once are answered
from `curated_urls` instead of from a fresh search.
