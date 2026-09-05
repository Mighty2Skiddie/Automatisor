-- Sector Analyst Agent — database schema
--
-- Three tables, normalised on the axis that actually matters here: a company's
-- identity is stable, its financials are a time series, and its soft facts
-- (headcount, hiring, news) are irregularly dated and qualitative. Collapsing
-- these into one wide table would let the agent quietly cite a stale number with
-- no way to say when it was true.
--
-- UNIT CONVENTIONS (the agent's prompt restates these; the guardrails depend on them)
--   market_cap, revenue, free_cash_flow ... USD, absolute
--   *_margin, revenue_growth, return_on_equity, dividend_yield
--                                       ... decimal fraction, so 0.462 means 46.2%
--   debt_to_equity                      ... ratio, so 1.54 means 1.54x
--                                           (Yahoo reports this as a percent; the
--                                            ingest divides by 100)
--   pe_ratio, ev_to_ebitda, beta        ... dimensionless
--
-- MISSING DATA IS NULL, NEVER 0. A zero margin and an unknown margin are different
-- facts, and conflating them is the single cheapest way to make this agent lie.

PRAGMA foreign_keys = ON;

-- Identity. Slow-changing, one row per company.
CREATE TABLE IF NOT EXISTS companies (
    ticker        TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    sector        TEXT NOT NULL,
    industry      TEXT,
    country       TEXT,
    source        TEXT NOT NULL,
    last_updated  TEXT NOT NULL              -- ISO-8601 date the row was written
);

CREATE INDEX IF NOT EXISTS ix_companies_sector ON companies (sector);

-- Numeric snapshots. One row per company per snapshot_date, so re-running the
-- build script on a later day appends history instead of destroying it.
CREATE TABLE IF NOT EXISTS financials (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker            TEXT NOT NULL REFERENCES companies (ticker) ON DELETE CASCADE,
    market_cap        REAL,
    revenue           REAL,
    revenue_growth    REAL,
    gross_margin      REAL,
    operating_margin  REAL,
    profit_margin     REAL,
    pe_ratio          REAL,
    ev_to_ebitda      REAL,
    debt_to_equity    REAL,
    free_cash_flow    REAL,
    return_on_equity  REAL,
    beta              REAL,
    dividend_yield    REAL,
    snapshot_date     TEXT NOT NULL,         -- ISO-8601 date the figures were pulled
    source            TEXT NOT NULL,
    UNIQUE (ticker, snapshot_date)           -- makes the ingest idempotent per day
);

CREATE INDEX IF NOT EXISTS ix_financials_ticker_date
    ON financials (ticker, snapshot_date DESC);

-- Soft facts: headcount, hiring signals, news. Separate because they are
-- qualitative, irregularly dated, and frequently undated at the source — which
-- the schema records honestly rather than papering over with the scrape date.
CREATE TABLE IF NOT EXISTS signals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT NOT NULL REFERENCES companies (ticker) ON DELETE CASCADE,
    signal_type   TEXT NOT NULL,             -- 'headcount' | 'hiring' | 'news'
    signal_value  TEXT NOT NULL,             -- human-readable form of the fact
    numeric_value REAL,                      -- machine-readable form where one exists
    as_of_date    TEXT,                      -- NULL when the source does not date it
    as_of_basis   TEXT NOT NULL,             -- how as_of_date was established, in words
    retrieved_on  TEXT NOT NULL,             -- ISO-8601 date we fetched it
    source        TEXT NOT NULL
);

-- COALESCE, not a plain UNIQUE: SQLite treats NULLs as distinct, so an undated
-- signal would duplicate on every re-run without this.
CREATE UNIQUE INDEX IF NOT EXISTS ux_signals_identity
    ON signals (ticker, signal_type, COALESCE(as_of_date, ''));

CREATE INDEX IF NOT EXISTS ix_signals_ticker_type ON signals (ticker, signal_type);
