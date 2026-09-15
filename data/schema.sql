-- Source Ledger schema: one row per search in `searches`, one row per URL in `search_urls`.
-- On the settings columns: NULL means the provider does not support that option,
-- "" means it does and the user left it unset. Collapsing the two would record a
-- filter that never ran.
CREATE TABLE IF NOT EXISTS searches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    query       TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    provider    TEXT,
    region      TEXT,
    safesearch  TEXT,
    timelimit   TEXT,
    backend     TEXT,
    max_results INTEGER
);
-- `provider` is denormalised from the parent search on purpose: after
-- dedupe_urls() collapses duplicates and the Spark job aggregates across
-- searches, "which corpus found this URL" must survive without a join.
-- There is deliberately no `engine` column — ddgs pools results from several
-- engines before returning them and discards which one produced each URL, so
-- any value here would be invented. `searches.backend` records the engines
-- asked, which is a different and weaker claim.
CREATE TABLE IF NOT EXISTS search_urls (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,
    url       TEXT    NOT NULL,
    provider  TEXT
);
CREATE INDEX IF NOT EXISTS idx_search_urls_search_id ON search_urls(search_id);
