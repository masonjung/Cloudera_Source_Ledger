"""Ingest — load the SQLite dev store into the raw Iceberg tables.

The bridge between Source Ledger's two storage tiers. Reads `data/source_ledger.db` (whatever
`app/server.py` has been writing) and appends it to `source_ledger.raw_searches` and
`source_ledger.raw_search_urls`, created by `data/iceberg/ddl.sql`.

Writes on invocation. Requires pyspark on the submit host, so this runs as a
Cloudera Data Engineering job or from a Cloudera AI session where Spark is on
the path:

    python data/ingest/load_to_iceberg.py                 # loads for real

Only links are ingested, never page content — see governance/DATA_CLASSIFICATION.md.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE.parent
DEFAULT_DB = DATA_DIR / "source_ledger.db"

# Column order here must match the Iceberg DDL. Kept explicit rather than
# SELECT * so a schema change fails loudly instead of silently misaligning.
SEARCH_COLUMNS = [
    "search_id", "query", "created_at", "provider", "region",
    "safesearch", "timelimit", "backend", "max_results",
]
URL_COLUMNS = ["search_id", "position", "url", "provider", "created_at"]

SEARCH_QUERY = """
    SELECT id AS search_id, query, created_at, provider, region,
           safesearch, timelimit, backend, max_results
      FROM searches
     WHERE created_at > ?
     ORDER BY id
"""

# `provider` comes off search_urls rather than the join, so a row carries its own
# provenance into the lakehouse instead of depending on the parent surviving.
URL_QUERY = """
    SELECT u.search_id, u.position, u.url, u.provider, s.created_at
      FROM search_urls u
      JOIN searches s ON s.id = u.search_id
     WHERE s.created_at > ?
     ORDER BY u.search_id, u.position
"""


def read_sqlite(db_path, since):
    """Pull searches and their URLs out of the dev store as lists of dicts."""
    if not db_path.exists():
        raise SystemExit(
            f"No SQLite store at {db_path}.\n"
            "Run the app first (make dev) so there is something to ingest."
        )
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        searches = [dict(r) for r in conn.execute(SEARCH_QUERY, (since,))]
        urls = [dict(r) for r in conn.execute(URL_QUERY, (since,))]
    finally:
        conn.close()
    return searches, urls


def load_to_iceberg(searches, urls, args):
    """Append both batches to Iceberg. Requires pyspark on the submit host."""
    try:
        from pyspark.sql import SparkSession
        from pyspark.sql import functions as F
    except ImportError:
        raise SystemExit(
            "This job needs pyspark. Run it as a Cloudera Data Engineering job, "
            "or from a Cloudera AI session where Spark is already on the path."
        )

    if not searches:
        print("Nothing to load - no searches past the watermark.")
        return

    spark = (
        SparkSession.builder
        .appName("source-ledger-ingest")
        .config("spark.sql.extensions",
                "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
        .getOrCreate()
    )
    prefix = f"{args.catalog}.{args.database}" if args.catalog else args.database

    def write(rows, columns, table):
        df = spark.createDataFrame([[r[c] for c in columns] for r in rows], columns)
        # created_at arrives as an ISO-8601 UTC string; Iceberg wants a real timestamp.
        df = (df.withColumn("created_at", F.to_timestamp("created_at"))
                .withColumn("ingested_at", F.current_timestamp()))
        df.writeTo(f"{prefix}.{table}").append()
        print(f"  appended {len(rows):>6} rows -> {prefix}.{table}")

    print(f"Source Ledger ingest -> {prefix}")
    write(searches, SEARCH_COLUMNS, "raw_searches")
    write(urls, URL_COLUMNS, "raw_search_urls")
    print("Done. Run pipelines/jobs/url_enrichment.py to refresh curated_urls.")
    spark.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help=f"SQLite dev store (default: {DEFAULT_DB})")
    parser.add_argument("--catalog", default="spark_catalog",
                        help="Iceberg catalog name (default: spark_catalog)")
    parser.add_argument("--database", default="source_ledger",
                        help="Target database (default: source_ledger)")
    parser.add_argument("--since", default="",
                        help="Only load searches with created_at greater than this "
                             "ISO-8601 UTC timestamp. Omit for a full load.")
    args = parser.parse_args(argv)

    searches, urls = read_sqlite(args.db, args.since)
    load_to_iceberg(searches, urls, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
