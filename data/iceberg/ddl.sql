-- Source Ledger — Iceberg DDL for the Lakehouse layer.
--
-- Mirrors the SQLite dev schema in ../schema.sql, plus the curated table that
-- pipelines/jobs/url_enrichment.py writes.
--
-- Apply with Spark SQL against the CDP catalog:
--   spark-sql --conf spark.sql.catalog.source_ledger=org.apache.iceberg.spark.SparkCatalog \
--             -f data/iceberg/ddl.sql
--
-- ${CATALOG} defaults to spark_catalog on CDW; substitute at submit time.
-- Every statement is IF NOT EXISTS so this file is safe to re-run — it is the
-- authoritative schema, not a one-shot script.

CREATE DATABASE IF NOT EXISTS source_ledger
COMMENT 'Source Ledger accelerator — search queries and the URLs they returned';

-- ---------------------------------------------------------------------------
-- Raw layer — a faithful copy of what the AI layer returned. Never edited in
-- place; corrections land as new rows so retrieval behaviour stays auditable.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS source_ledger.raw_searches (
    search_id     BIGINT   COMMENT 'Matches searches.id in the SQLite dev store',
    query         STRING   COMMENT 'The natural-language text the user submitted',
    created_at    TIMESTAMP COMMENT 'UTC. Source of truth for partitioning.',
    provider      STRING   COMMENT 'Corpus searched: ddgs | wikipedia | openalex | arxiv',
    region        STRING   COMMENT 'Locale code (ddgs) or language edition (wikipedia). NULL = provider does not support it',
    safesearch    STRING   COMMENT 'off | moderate | on. NULL = provider does not support it',
    timelimit     STRING   COMMENT 'd | w | m | y. "" = supported but unused, NULL = provider does not support it',
    backend       STRING   COMMENT 'Comma-delimited engines ASKED (ddgs only). Not which engine answered - ddgs does not report that',
    max_results   INT      COMMENT 'Result ceiling requested, 1-50',
    ingested_at   TIMESTAMP COMMENT 'When this row reached the lakehouse'
)
USING iceberg
PARTITIONED BY (days(created_at))
TBLPROPERTIES (
    'format-version' = '2',
    'write.format.default' = 'parquet',
    'write.parquet.compression-codec' = 'zstd',
    -- Searches are small and arrive steadily; compact aggressively so the
    -- partition does not fill with tiny files.
    'write.target-file-size-bytes' = '134217728',
    'history.expire.max-snapshot-age-ms' = '604800000'
);

CREATE TABLE IF NOT EXISTS source_ledger.raw_search_urls (
    search_id     BIGINT   COMMENT 'FK to raw_searches.search_id',
    position      INT      COMMENT 'Rank order as returned by the engine, 0-based',
    url           STRING   COMMENT 'Result URL. Links only - never page content.',
    provider      STRING   COMMENT 'Corpus that returned this URL, denormalised from the parent search',
    created_at    TIMESTAMP COMMENT 'Denormalised from the parent search for partitioning',
    ingested_at   TIMESTAMP COMMENT 'When this row reached the lakehouse'
)
USING iceberg
PARTITIONED BY (days(created_at))
TBLPROPERTIES (
    'format-version' = '2',
    'write.format.default' = 'parquet',
    'write.parquet.compression-codec' = 'zstd',
    'write.target-file-size-bytes' = '134217728',
    'history.expire.max-snapshot-age-ms' = '604800000'
);

-- ---------------------------------------------------------------------------
-- Curated layer — written by pipelines/jobs/url_enrichment.py.
--
-- One row per distinct URL across all searches, which is the lakehouse form of
-- what db.dedupe_urls() does locally. Bucketed rather than date-partitioned:
-- this table is queried by domain and joined on url, not scanned by day.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS source_ledger.curated_urls (
    url             STRING  COMMENT 'Normalised URL - lowercased host, tracking params stripped',
    domain          STRING  COMMENT 'Registrable host, e.g. docs.cloudera.com',
    tld             STRING  COMMENT 'Top-level domain, e.g. com',
    scheme          STRING  COMMENT 'http | https',
    first_seen      TIMESTAMP COMMENT 'Earliest search that returned this URL',
    last_seen       TIMESTAMP COMMENT 'Most recent search that returned it',
    times_seen      INT     COMMENT 'How many distinct searches returned it',
    best_position   INT     COMMENT 'Best (lowest) rank achieved across those searches',
    search_ids      ARRAY<BIGINT> COMMENT 'Every search that returned this URL',
    providers       ARRAY<STRING> COMMENT 'Every corpus that returned this URL. More than one entry means independent corroboration',
    processed_at    TIMESTAMP COMMENT 'When the enrichment job last wrote this row'
)
USING iceberg
PARTITIONED BY (bucket(16, domain))
TBLPROPERTIES (
    'format-version' = '2',
    'write.format.default' = 'parquet',
    'write.parquet.compression-codec' = 'zstd',
    -- Row-level deletes: the enrichment job MERGEs on url rather than rewriting
    -- whole partitions, so merge-on-read keeps the write cheap.
    'write.delete.mode' = 'merge-on-read',
    'write.update.mode' = 'merge-on-read',
    'write.merge.mode' = 'merge-on-read'
);
