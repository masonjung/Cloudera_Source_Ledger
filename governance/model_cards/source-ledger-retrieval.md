# Model card — Source Ledger retrieval

| | |
|---|---|
| **Component** | `retrieval/source_ledger.py` → `text_to_urls()`, dispatching to `retrieval/providers.py` |
| **Version** | Tracked by git commit; no separate model version |
| **Owner** | Accelerator owner (see [`docs/GATES.md`](../../docs/GATES.md)) |
| **Status** | Reference implementation, not customer-deployed |
| **Last reviewed** | Not yet reviewed — due at the Harden gate |

## What this is, and what it is not

**It is a metasearch client, not a trained model.** There are no weights, no
training data, no fine-tuning, and no inference of the customer's own. Source Ledger sends
a query to a third-party search service and returns the URLs it ranks — to web
engines through the `ddgs` library, or to the Wikipedia, OpenAlex, and arXiv APIs
directly.

It gets a model card anyway, because from a governance standpoint it behaves like
one: an opaque third-party system converts user text into ranked output that drives
downstream decisions, and neither the ranking function nor its failure modes are
inspectable. The accountability questions are identical even though the mechanism
is not.

If retrieval later grows a genuine model — an LLM reranker, query expansion, an
agent loop — that component needs its **own** card. Do not extend this one.

## Intended use

Discovery and research: turning a natural-language information need into a
navigable set of source links, with the query and its options recorded so the search
is reproducible.

**Out of scope, explicitly:**

- **Exhaustive retrieval.** Results are what a public engine ranked in the top N.
  Absence of a URL is not evidence the page does not exist. Never use Source Ledger output
  to conclude "there is no such document."
- **Ranking as authority.** `best_position` reflects engine SEO ranking, not
  credibility, recency, or correctness.
- **Compliance or legal search** where completeness is a requirement.
- **Any deployment where query text cannot leave the environment** — see
  [`../DATA_CLASSIFICATION.md`](../DATA_CLASSIFICATION.md#third-party-disclosure).

## Inputs and outputs

**In:** natural-language text, plus `provider`, `max_results` (1–50), `region`,
`safesearch`, `timelimit`, `backend`. Every option is whitelist-validated in
`app/server.py` before it reaches this component.

One search uses one provider. The four options above the first two are **not
universally supported**, and the support matrix in `retrieval/providers.py` is what decides:

| Provider | Corpus | `region` | `timelimit` | `safesearch` | `backend` |
|---|---|---|---|---|---|
| `ddgs` | Web (DuckDuckGo, Yahoo, Startpage, Yandex) | locale bias | ✓ | ✓ | engine chain |
| `wikipedia` | Encyclopedia, via MediaWiki | language edition | — | — | — |
| `openalex` | Scholarly works | — | publication date | — | — |
| `arxiv` | Preprints | — | submission date | — | — |

An option a provider does not support is **dropped before the call and persisted as
`NULL`**, never as the value the form posted. That distinction is the basis of the
reproducibility claim below: a stored `NULL` means "this corpus has no such filter",
a stored `""` means "it has one and this search did not use it."

`region` is deliberately not offered for OpenAlex. It does expose a country filter,
but on author affiliation rather than the locale of the work — surfacing it under
the control that means "result locale" elsewhere would misrepresent what was
applied.

**Out:** a list of URL strings, deduplicated, in engine rank order. Empty list when
no engine returns anything — which is indistinguishable from "no such page exists,"
and is the component's most important failure mode.

## Known limitations and risks

| Risk | Detail | Mitigation |
|---|---|---|
| **Silent empty results** | A throttled engine returns `[]`, identical to a genuine no-match | Up to four engines per `ddgs` search; `app/server.py` surfaces "No results found" rather than an empty success |
| **Engine attribution is not recoverable** | ddgs pools results from several engines and discards which one produced each URL. `backend` therefore records the engines **asked**, not the engine that answered — a weaker claim than it looks. | `raw_search_urls.provider` records the corpus truthfully, at the level where it is actually known. No per-URL engine column exists, because it could only be fabricated |
| **Selected engines are not all consulted** | ddgs submits engines concurrently and appears to drop results from any that do not return inside its first wait. Observed 2026-08-09: `yahoo` alone gave 7 URLs, `startpage` alone gave 10, `yahoo,startpage` gave 7. Selecting more engines is not reliably more coverage. | Measure with the eval notebook rather than assuming; the behaviour is ddgs's, not this repo's, and is documented in `docs/ARCHITECTURE.md` |
| **Correlated failure across the `ddgs` chain** | The four web engines are reached by one mechanism — requesting public result pages. Rate limiting, IP blocking, and markup changes hit all four together, so a chain of four is not four independent chances. A datacenter egress IP is the profile these engines block hardest, so a search that works on a laptop can return `[]` from a CML session. | The three API providers do not share the mechanism and are not blocked by IP reputation. Selecting one is the mitigation; per-provider availability is measured in the live test tier |
| **Corpus mismatch** | A provider answers only from its own corpus. arXiv has no opinion on a product question and Wikipedia none on a preprint. An unhelpful answer looks identical to an unavailable one. | Providers are a user-visible choice, not a silent fallback: nothing re-routes a query to a different corpus. The provider is recorded on every search |
| **Third-party ranking bias** | Ranking encodes commercial SEO and each provider's own editorial choices. Source Ledger inherits all of it and cannot inspect it. | Multi-engine chain and multi-provider choice reduce single-source dependence; overlap measured in `retrieval/notebooks/eval.ipynb` |
| **Geographic and language skew** | `region` materially changes results; `wt-wt` default skews English. For Wikipedia it selects the language edition, which is a different corpus rather than a different ranking of the same one. | Region is user-selectable and recorded per search — as `NULL` where the provider does not apply it |
| **Options that do not apply** | A UI that offers a time window to a corpus with no date filter would record a filter that never ran | Support matrix drives the call, the persisted record, and which controls render. Unsupported options are stored `NULL`; asserted by `tests/test_server.py::test_unsupported_options_are_stored_null_not_as_posted` |
| **Non-reproducibility** | Engines re-rank continuously. The same query tomorrow returns different URLs. | Every search persists its full option set and timestamp, so a result set is explainable even when it is not repeatable |
| **Availability drift** | Engines add rate limits and blocks without notice | Re-run the eval notebook before changing defaults; treat availability as a monitored property |
| **A block can look like an empty corpus** | ddgs reports "every engine failed" as an empty search, and an engine that answers HTTP 200 with a challenge page raises nothing at all. Untreated, a dead network renders as "No results found." — a false negative the record cannot distinguish from a real one. | `EngineError` names each failing engine and its reason; an empty search that consumed the whole collection window is reported as a timeout rather than a miss. The 200-with-a-challenge-page case remains undetectable in-band — `make doctor` probes each engine individually to cover it out of band |
| **No content safety on targets** | `safesearch` is applied by the engine; Source Ledger does not inspect the pages. **Only `ddgs` supports it at all** — the three API providers have no equivalent, and their corpora are curated rather than open web. | Defaults to `moderate` where supported, `NULL` where not; never fetches page content |
| **Politeness obligations** | Wikipedia, OpenAlex, and arXiv publish rate-limit and identification expectations. Ignoring them looks like a broken provider, not a blocked one. | `SOURCE_LEDGER_CONTACT` sets the `User-Agent` and OpenAlex `mailto`; unset degrades to the anonymous pool rather than failing |
| **Yandex data residency** | Operated from Russia; excluded by some customers' rules | Off by default in the UI |

## Evaluation

Measured by [`retrieval/notebooks/eval.ipynb`](../../retrieval/notebooks/eval.ipynb) —
availability, mean yield, and pairwise engine overlap.

> **No results recorded yet.** Run the notebook and paste the availability and
> overlap tables below with the date. Engine behaviour drifts, so an undated
> measurement is not evidence — a result older than a quarter should be re-run
> before it is relied on.

| Date | Provider / engine | Availability | Mean yield | Notes |
|---|---|---|---|---|
| _pending_ | `ddgs` / duckduckgo | — | — | Current default |
| _pending_ | `ddgs` / yahoo | — | — | |
| _pending_ | `ddgs` / startpage | — | — | |
| _pending_ | `ddgs` / yandex | — | — | Off by default |
| _pending_ | `wikipedia` | — | — | |
| _pending_ | `openalex` | — | — | |
| _pending_ | `arxiv` | — | — | |

Overlap between the API providers and the web engines is expected to be near zero.
That is the argument for offering them — they widen coverage rather than re-returning
what DuckDuckGo already found — and simultaneously the reason they are **not** links
in the `backend` fallback chain: near-zero overlap is exactly what makes a corpus a
bad substitute when another is throttled.

The gate for changing the default `backend` chain is in
[`docs/GATES.md`](../../docs/GATES.md) under **Harden**.

### Verified behaviour, 2026-08-09

Not an availability run — the numbers above are still pending. These are the
per-provider option semantics, checked against the live APIs when the seam was
built, and each is pinned by a test:

| Check | Result |
|---|---|
| Wikipedia `region` selects the language edition | ✓ `kr-kr` → `ko.wikipedia.org`, `jp-jp` → `ja.wikipedia.org` |
| OpenAlex `timelimit` applies a publication-date filter | ✓ results confined to the window |
| arXiv `timelimit` applies a submission-date filter | ✓ after the grouping fix below |
| Unsupported options never reach a provider | ✓ asserted on the outbound request, not just the return value |

**Defect found and fixed during this work.** arXiv binds `AND` to the last term of
an unparenthesised multi-word query, so `all:apache iceberg AND submittedDate:[…]`
filtered on "iceberg" alone and returned 2013 papers for a "past month" search. The
response carried no indication the filter had been dropped — it looked like a
successful, filtered search. The term clause is now parenthesised. This is the
failure mode this card calls **Options that do not apply**, occurring inside a
provider rather than at the UI boundary, and it is the reason the tests assert on
the request that goes out rather than the results that come back.

## Ethical and operational considerations

Source Ledger queries public endpoints without an API key or account. That makes it free to
run and free of vendor lock-in; it also means usage is bounded by each provider's
tolerance rather than by a contract. The `COOLDOWN_S` pause in the eval notebook and
the 50-result ceiling in `app/server.py` exist for that reason. Removing them shifts
cost onto providers who have not agreed to carry it.

That obligation is not uniform, and a reviewer should be told which kind they are
relying on. `ddgs` is free because it is *unofficial* — no counterparty, no terms,
tolerance that can end without notice. Wikipedia, OpenAlex, and arXiv are free
because free access is the operators' stated mission: published terms, documented
rate limits, and an explicit invitation to call them programmatically. Both are $0;
only the second is something to build a customer deployment on. Neither carries a
data-processing agreement, so neither answers the constraint in
[`../DATA_CLASSIFICATION.md`](../DATA_CLASSIFICATION.md#third-party-disclosure).

The three API providers ask callers to identify themselves. `SOURCE_LEDGER_CONTACT` is how
that is supplied, and it should be a team or service address: it makes queries
attributable at the receiving end even though they stay unattributable in Source Ledger's
own tables.

Users see the URLs, not the mechanism. The Serve layer records and displays which
provider, engine, region, and time window produced each result set — including the
distinction between an option that was unavailable and one that was simply unused.
Keep both visible if the UI is rebuilt; collapsing them would make every search
record slightly untrue.

## Change log

| Date | Change | Rationale |
|---|---|---|
| 2026-08-04 | Card created during the Forge layout restructure | Retrieval had no governance record |
| 2026-08-09 | Added `wikipedia`, `openalex`, and `arxiv` as selectable providers alongside `ddgs`; defaults unchanged | The four `ddgs` engines share one access mechanism and fail together, most likely from the datacenter IPs a CDP deployment uses. The new providers are official APIs that do not share that failure mode |
| 2026-08-09 | Unsupported options now persist as `NULL` rather than as posted | A search record that reports a filter the provider never applied is not reproducible, which is the property this card claims for it |
| 2026-08-09 | Fixed arXiv date filtering silently not applying to multi-word queries | Recorded above under Verified behaviour; found by checking the outbound request against the live API |
| 2026-08-09 | Added per-URL `provider` to `raw_search_urls` and `providers` to `curated_urls` | Provenance survives deduplication and the curated aggregation without a join back to the search |
| 2026-08-09 | Corrected the documented `ddgs` chain semantics; recorded that engine-level attribution is unobtainable | The previous description (sequential fall-through, load-bearing order) did not match ddgs 9.x, and it was the basis for reading `backend` as "which engine answered" |

Record every change to a retrieval default here, with the eval run that justified it.
