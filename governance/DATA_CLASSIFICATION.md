# Source Ledger — Data classification

What this accelerator stores, what it deliberately does not, and how each field is
classified. This is the document a customer's data-protection reviewer reads first.

## The one-line commitment

**Source Ledger stores links, never page content.** It records the query a user submitted
and the URLs a search engine returned. It does not fetch, render, cache, or persist
the pages behind those URLs.

That is enforced structurally, not by policy: retrieval reads only the URL field
from each result and discards the rest of the response. **No HTTP client in this
repo fetches the content of a result URL.** Any change that adds one is a
classification change and needs this document updated first.

Since the provider seam was added, that claim needs one distinction drawn precisely.
`retrieval/providers.py` does make outbound HTTP requests — to the Wikipedia, OpenAlex, and
arXiv *metadata* APIs, through a single seam (`_get_json` / `_get_bytes`). Those
responses contain snippets and abstracts. Source Ledger reads the URL out of each record and
discards everything else, exactly as it discards everything but `href` from a `ddgs`
result. So the commitment is unchanged in substance — a search result's *description*
is not the *page behind it*, and neither is stored — but the mechanism is no longer
"there is no HTTP client here." It is now: there is one HTTP seam, it calls search
and metadata APIs only, and it never dereferences a result URL. A change that points
that seam at a result URL is a classification change.

## Inventory

| Field | Table | Classification | Why |
|---|---|---|---|
| `query` | `raw_searches` | **Confidential** | Free text typed by a user. Users put anything in a search box — assume it can contain personal or commercially sensitive detail even though the UI never asks for it. |
| `created_at` | `raw_searches` | Internal | Behavioural timestamp. Combined with `query`, reveals a user's activity pattern. |
| `provider`, `region`, `safesearch`, `timelimit`, `backend`, `max_results` | `raw_searches` | Internal | Configuration, not user data. `region` is a weak locality signal. `provider` records which corpus was searched; a `NULL` in any of the others means that provider does not support the option, not that it was left unset. |
| `url` | `raw_search_urls`, `curated_urls` | Public | Public web addresses returned by a public search engine. |
| `position` | `raw_search_urls` | Public | Engine ranking. |
| `provider` | `raw_search_urls` | Internal | Which corpus returned this URL, denormalised from the parent search so provenance survives deduplication. Configuration, not user data. |
| `domain`, `tld`, `scheme` | `curated_urls` | Public | Derived from `url`. |
| `times_seen`, `first_seen`, `last_seen`, `best_position`, `providers` | `curated_urls` | Internal | Aggregate popularity across searches. `providers` lists every corpus that returned the URL. |

**The sensitive column is `query`, and only `query`.** Everything else is public web
data or configuration. Access control should be designed around that single fact —
see `sdx/ranger-policies.json`, which masks `query` for the analyst role while
leaving the URL tables readable.

## Personal data

Source Ledger as shipped stores **no identity fields**: no user ID, no session ID, no IP
address, no authentication subject. A search cannot be attributed to a person from
the data alone.

This is a real property of the current schema, and it is fragile. Adding a
`user_id` column — the obvious next feature for a multi-tenant deployment — changes
this accelerator's data-protection posture materially. If you add one:

1. Classify it **Confidential** and update this table.
2. Add a Ranger masking policy for it alongside the `query` policy.
3. Add a retention job — attributable search history needs a defined lifespan.
4. Re-run the Harden gate in [`docs/GATES.md`](../docs/GATES.md).

## Third-party disclosure

Queries are sent to external services over their public endpoints. **The query text
leaves the customer's environment**, whichever provider is selected.

| Provider | Reached via | Operator | Jurisdiction |
|---|---|---|---|
| DuckDuckGo, Yahoo, Startpage, Yandex | `ddgs`, scraping public result pages | Commercial search engines | Mixed — see the Yandex note below |
| Wikipedia | MediaWiki API | Wikimedia Foundation (non-profit) | US |
| OpenAlex | REST API | OurResearch (non-profit) | US |
| arXiv | Atom API | Cornell University | US |

There is no API key and no account for any of them, so there is no contractual
data-processing agreement, and their handling is governed by their own privacy
policies rather than the customer's. For deployments where query text cannot leave
the environment, Source Ledger is not appropriate without swapping `retrieval/providers.py` for an
internal index.

The two rows differ in kind, and a reviewer should be told which one they are
getting. The `ddgs` row reaches engines by requesting public result pages without a
sanctioned interface; the three API rows are documented, versioned interfaces
operated by non-profits whose published terms invite programmatic use. Neither
carries a DPA, but only the first depends on continued tolerance.

Note that Yandex is operated from Russia. Some customers' data-residency rules
exclude it outright; it is off by default in the UI and should stay off unless a
customer explicitly enables it.

### The `SOURCE_LEDGER_CONTACT` identifier

Wikipedia, OpenAlex, and arXiv each ask callers to identify themselves — Wikipedia
through a descriptive `User-Agent`, OpenAlex through a `mailto` that admits you to
its faster "polite pool." Source Ledger sends the value of the `SOURCE_LEDGER_CONTACT` environment
variable for both, and sends nothing when it is unset.

This is a disclosure change, not a schema change: **nothing new is stored**, but an
operator-chosen identifier is now attached to outbound queries. If that value is a
personal address, queries become attributable to a person at the receiving end even
though they remain unattributable in Source Ledger's own tables. Use a team or service
address. Leaving it unset is supported and degrades to the anonymous rate-limit
pool rather than failing.

## Retention

| Store | Default retention | Mechanism |
|---|---|---|
| SQLite dev store (`data/source_ledger.db`) | None — grows until cleared | `Clear all` in the UI, or delete the file |
| `raw_searches`, `raw_search_urls` | 7 days of Iceberg snapshots | `history.expire.max-snapshot-age-ms` in `data/iceberg/ddl.sql` |
| `curated_urls` | Indefinite | Aggregated public data, no expiry configured |

Snapshot expiry governs *time travel*, not the rows themselves — expiring snapshots
does not delete current data. **There is no row-level retention policy on
`raw_searches` today.** A deployment with a query-retention requirement needs a
scheduled `DELETE FROM source_ledger.raw_searches WHERE created_at < …` job added to
`pipelines/`; that is a known gap, not an oversight.

## Lineage

Atlas captures lineage automatically for the Spark paths:

```
search engines / metadata APIs → retrieval/providers.py → retrieval/source_ledger.py → SQLite
    → data/ingest/load_to_iceberg.py → raw_searches / raw_search_urls
    → pipelines/jobs/url_enrichment.py → curated_urls
```

The first hop is outside Atlas's view — the provider call is an ordinary HTTPS
request from the Serve layer, so provenance for "what produced this URL" comes from
the `provider` and `backend` columns, not from lineage metadata. Keep both populated.

Provenance resolves to the **corpus**, and stops there:

| Question | Answered by |
|---|---|
| Which corpus returned this URL? | `raw_search_urls.provider` |
| Which corpora have ever returned it? | `curated_urls.providers` |
| Which engines were asked for this search? | `raw_searches.backend` |
| Which engine returned this URL? | **Not recorded, and not recoverable** |

The last row is a property of `ddgs`, not a gap in the schema. It pools results from
several engines into one list and discards which engine produced each row before
returning, so a per-URL engine column could only be populated with a guess. Reading
`backend` as "the engine that answered" is therefore wrong: it is the engines that
were *asked*, and ddgs does not consult all of them reliably. Recovering true
engine-level attribution would mean querying each engine separately and attributing
the results in this repo — a retrieval behaviour change, not a schema change.

`curated_urls.providers` accumulates rather than collapses. The local
`db.dedupe_urls()` keeps one row per URL and discards the other sighting, so it is
lossy for provenance by design; the lakehouse path keeps every sighting. A URL with
more than one entry in `providers` was found independently by more than one corpus.
