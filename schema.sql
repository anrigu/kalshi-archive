-- Kalshi archive schema (SQLite). Idempotent: safe to re-apply.
-- Timestamps are ISO-8601 UTC strings as served by the API; prices/volumes
-- are REAL dollars/contracts.

CREATE TABLE IF NOT EXISTS series (
    ticker      TEXT PRIMARY KEY,
    title       TEXT,
    category    TEXT,
    frequency   TEXT,
    raw         TEXT NOT NULL  -- full API JSON
);

CREATE TABLE IF NOT EXISTS events (
    event_ticker       TEXT PRIMARY KEY,
    series_ticker      TEXT,
    title              TEXT,
    sub_title          TEXT,
    category           TEXT,
    mutually_exclusive INTEGER,
    raw                TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_series_idx ON events (series_ticker);

CREATE TABLE IF NOT EXISTS markets (
    ticker             TEXT PRIMARY KEY,
    event_ticker       TEXT,
    market_type        TEXT,
    title              TEXT,
    status             TEXT,
    result             TEXT,
    open_time          TEXT,
    close_time         TEXT,
    expiration_time    TEXT,
    volume             REAL,
    open_interest      REAL,
    liquidity_dollars  REAL,
    last_price_dollars REAL,
    raw                TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS markets_event_idx ON markets (event_ticker);
CREATE INDEX IF NOT EXISTS markets_status_idx ON markets (status);
CREATE INDEX IF NOT EXISTS markets_close_idx ON markets (close_time);

CREATE TABLE IF NOT EXISTS trades (
    trade_id           TEXT PRIMARY KEY,
    ticker             TEXT NOT NULL,
    created_time       TEXT NOT NULL,
    yes_price_dollars  REAL,
    no_price_dollars   REAL,
    count              REAL,
    taker_side         TEXT,
    taker_outcome_side TEXT,
    taker_book_side    TEXT,
    is_block_trade     INTEGER
);
CREATE INDEX IF NOT EXISTS trades_ticker_time_idx ON trades (ticker, created_time);
CREATE INDEX IF NOT EXISTS trades_time_idx ON trades (created_time);

CREATE TABLE IF NOT EXISTS candlesticks (
    ticker          TEXT NOT NULL,
    period_interval INTEGER NOT NULL,      -- minutes: 1 or 60
    end_period_ts   INTEGER NOT NULL,      -- unix seconds, end of the period
    price_open      REAL,
    price_high      REAL,
    price_low       REAL,
    price_close     REAL,
    price_mean      REAL,
    yes_bid_open    REAL,
    yes_bid_high    REAL,
    yes_bid_low     REAL,
    yes_bid_close   REAL,
    yes_ask_open    REAL,
    yes_ask_high    REAL,
    yes_ask_low     REAL,
    yes_ask_close   REAL,
    volume          REAL,
    open_interest   REAL,
    PRIMARY KEY (ticker, period_interval, end_period_ts)
);

-- Resumable-backfill bookkeeping.
CREATE TABLE IF NOT EXISTS checkpoints (
    stage      TEXT PRIMARY KEY,
    state      TEXT NOT NULL   -- JSON
);

CREATE TABLE IF NOT EXISTS candle_progress (
    ticker    TEXT PRIMARY KEY,
    done      INTEGER NOT NULL DEFAULT 0,
    n_candles INTEGER,
    error     TEXT
);
