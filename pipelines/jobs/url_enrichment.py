"""Process layer — normalise, deduplicate, and enrich raw search URLs.

Reads `source_ledger.raw_search_urls` + `source_ledger.raw_searches`, and MERGEs one row per
distinct URL into `source_ledger.curated_urls`. This is the lakehouse form of what
`db.dedupe_urls()` does locally: keep one record per URL, remember the earliest
sighting, drop the rest.

    python pipelines/jobs/url_enrichment.py                # runs it (needs Spark)

The normalisation helpers below are deliberately pure Python with no Spark
dependency, so `tests/data_quality/` can exercise them on a laptop. Everything
Spark-specific lives under `run()`.
"""

import argparse
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query parameters that identify a click, not a document. Two URLs differing
# only by these are the same page, so they must collapse to one curated row.
TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_source_platform", "utm_creative_format",
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "twclid", "igshid",
    "mc_cid", "mc_eid", "_hsenc", "_hsmi", "vero_id", "yclid", "ref_src",
})

# Hosts whose "www." prefix is cosmetic. Stripped so www.example.com and
# example.com do not both survive deduplication.
WWW_PREFIX = "www."


def normalize_url(url):
    """Collapse cosmetic differences so equal pages compare equal.

    Lowercases scheme and host, strips a leading `www.`, drops tracking
    parameters and fragments, and removes a trailing slash on the path.
    Returns the input unchanged if it does not parse as an absolute URL.
    """
    if not url or not isinstance(url, str):
        return url
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.scheme or not parts.netloc:
        return url  # relative or malformed — leave it alone rather than mangle it

    host = parts.hostname or ""
    if host.startswith(WWW_PREFIX):
        host = host[len(WWW_PREFIX):]
    # Preserve a non-default port; drop 80/443 since they are implied.
    netloc = host
    if parts.port and parts.port not in (80, 443):
        netloc = f"{host}:{parts.port}"

    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS]
    path = parts.path.rstrip("/") or "/"

    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(kept), ""))


def url_parts(url):
    """Split a URL into the (domain, tld, scheme) columns of `curated_urls`."""
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return None, None, None
    host = (parts.hostname or "").lower()
    if host.startswith(WWW_PREFIX):
        host = host[len(WWW_PREFIX):]
    tld = host.rsplit(".", 1)[-1] if "." in host else None
    return (host or None), tld, (parts.scheme.lower() or None)


# The MERGE is expressed as SQL rather than a DataFrame write so the statement
# that runs is the statement you can read here — no hidden plan.
#
# Every SET below is idempotent: LEAST/GREATEST for the bounds, array_distinct for
# the collections, and `times_seen` derived from the deduplicated `search_ids`
# rather than summed. It has to be. The CDE schedule runs this job nightly with no
# --since (pipelines/cde/url_enrichment.job.yaml), so every run re-stages the whole
# raw table; `target.times_seen + source.times_seen` counted the same sightings
# again every night, and the column drifted away from its own DDL definition --
# "how many distinct searches returned it" -- by one full recount per day.
# size(array_distinct(...)) restates that definition in the SQL, so a re-run, an
# overlapping --since backfill, and a retry after a failure all converge.
MERGE_SQL = """
MERGE INTO {prefix}.curated_urls AS target
USING staged_urls AS source
   ON target.url = source.url
 WHEN MATCHED THEN UPDATE SET
      target.last_seen     = GREATEST(target.last_seen, source.last_seen),
      target.first_seen    = LEAST(target.first_seen, source.first_seen),
      target.times_seen    = size(array_distinct(
                                 concat(target.search_ids, source.search_ids))),
      target.best_position = LEAST(target.best_position, source.best_position),
      target.search_ids    = array_distinct(
                                 concat(target.search_ids, source.search_ids)),
      target.providers     = array_distinct(
                                 concat(target.providers, source.providers)),
      target.processed_at  = source.processed_at
 WHEN NOT MATCHED THEN INSERT *
"""

# Column order must match curated_urls in data/iceberg/ddl.sql — `INSERT *`
# binds positionally, so a reordering here misaligns silently.
#
# `providers` accumulates rather than collapses: unlike db.dedupe_urls(), which
# keeps one row and discards the other sighting, the lakehouse keeps every
# sighting and records which corpora produced it. A URL with more than one entry
# was found independently by more than one corpus, which is a stronger signal
# than the same URL twice from one engine.
#
# collect_list drops NULLs, so rows ingested before per-URL provenance existed
# contribute an empty array rather than a fabricated value.
STAGE_SQL = """
SELECT url,
       domain,
       tld,
       scheme,
       MIN(created_at)          AS first_seen,
       MAX(created_at)          AS last_seen,
       COUNT(DISTINCT search_id) AS times_seen,
       MIN(position)            AS best_position,
       array_distinct(collect_list(search_id)) AS search_ids,
       array_distinct(collect_list(provider))  AS providers,
       current_timestamp()      AS processed_at
  FROM normalised
 WHERE url IS NOT NULL AND domain IS NOT NULL
 GROUP BY url, domain, tld, scheme
"""


def run(args):
    """Execute the enrichment against Iceberg. Requires Spark."""
    try:
        from pyspark.sql import SparkSession
        from pyspark.sql import functions as F
        from pyspark.sql.types import StringType, StructField, StructType
    except ImportError:
        raise SystemExit(
            "This job needs pyspark. Submit it through Cloudera Data Engineering "
            "(see pipelines/cde/url_enrichment.job.yaml) or run it from a Cloudera AI "
            "session."
        )

    prefix = f"{args.catalog}.{args.database}" if args.catalog else args.database
    spark = (
        SparkSession.builder
        .appName("source-ledger-url-enrichment")
        .config("spark.sql.extensions",
                "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
        .getOrCreate()
    )

    # normalize_url and url_parts are shipped as UDFs so the same code path that
    # tests/ verifies is the one that runs on the cluster.
    norm_udf = F.udf(normalize_url, StringType())
    parts_udf = F.udf(url_parts, StructType([
        StructField("domain", StringType()),
        StructField("tld", StringType()),
        StructField("scheme", StringType()),
    ]))

    raw = spark.table(f"{prefix}.raw_search_urls")
    if args.since:
        raw = raw.where(F.col("created_at") > F.lit(args.since).cast("timestamp"))

    normalised = (
        raw.withColumn("url", norm_udf(F.col("url")))
           .withColumn("_p", parts_udf(F.col("url")))
           .select("search_id", "position", "url", "provider", "created_at",
                   F.col("_p.domain").alias("domain"),
                   F.col("_p.tld").alias("tld"),
                   F.col("_p.scheme").alias("scheme"))
    )
    normalised.createOrReplaceTempView("normalised")

    staged = spark.sql(STAGE_SQL)
    staged.createOrReplaceTempView("staged_urls")
    staged_count = staged.count()

    spark.sql(MERGE_SQL.format(prefix=prefix))

    total = spark.table(f"{prefix}.curated_urls").count()
    print(f"staged {staged_count} distinct URLs -> {prefix}.curated_urls "
          f"({total} rows total)")
    spark.stop()


def build_parser():
    """The CLI contract. Separate from main() so a test can exercise it
    without running the job -- pipelines/cde/url_enrichment.job.yaml
    hard-codes the args CDE passes, and nothing else checks they agree."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--catalog", default="spark_catalog",
                        help="Iceberg catalog name (default: spark_catalog)")
    parser.add_argument("--database", default="source_ledger",
                        help="Target database (default: source_ledger)")
    parser.add_argument("--since", default="",
                        help="Only process rows with created_at greater than this "
                             "ISO-8601 UTC timestamp. Omit for a full rebuild.")
    return parser


def main(argv=None):
    run(build_parser().parse_args(argv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
