-- Kalshi archive schema. Idempotent: safe to re-apply.

CREATE TABLE IF NOT EXISTS series (
    ticker      text PRIMARY KEY,
    title       text,
    category    text,
    frequency   text,
    raw         jsonb NOT NULL,
    fetched_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS events (
    event_ticker      text PRIMARY KEY,
    series_ticker     text,
    title             text,
    sub_title         text,
    category          text,
    mutually_exclusive boolean,
    raw               jsonb NOT NULL,
    fetched_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_series_idx ON events (series_ticker);

CREATE TABLE IF NOT EXISTS markets (
    ticker            text PRIMARY KEY,
    event_ticker      text,
    market_type       text,
    title             text,
    status            text,
    result            text,
    open_time         timestamptz,
    close_time        timestamptz,
    expiration_time   timestamptz,
    volume            numeric,
    open_interest     numeric,
    liquidity_dollars numeric,
    last_price_dollars numeric,
    raw               jsonb NOT NULL,
    fetched_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS markets_event_idx ON markets (event_ticker);
CREATE INDEX IF NOT EXISTS markets_status_idx ON markets (status);
CREATE INDEX IF NOT EXISTS markets_close_idx ON markets (close_time);

CREATE TABLE IF NOT EXISTS trades (
    trade_id          uuid PRIMARY KEY,
    ticker            text NOT NULL,
    created_time      timestamptz NOT NULL,
    yes_price_dollars numeric,
    no_price_dollars  numeric,
    count             numeric,
    taker_side        text,
    taker_outcome_side text,
    taker_book_side   text,
    is_block_trade    boolean
);
CREATE INDEX IF NOT EXISTS trades_ticker_time_idx ON trades (ticker, created_time);
CREATE INDEX IF NOT EXISTS trades_time_idx ON trades (created_time);

CREATE TABLE IF NOT EXISTS candlesticks (
    ticker          text NOT NULL,
    period_interval int NOT NULL,          -- minutes: 1, 60, or 1440
    end_period_ts   bigint NOT NULL,       -- unix seconds, end of the period
    price_open      numeric,
    price_high      numeric,
    price_low       numeric,
    price_close     numeric,
    price_mean      numeric,
    yes_bid_open    numeric,
    yes_bid_high    numeric,
    yes_bid_low     numeric,
    yes_bid_close   numeric,
    yes_ask_open    numeric,
    yes_ask_high    numeric,
    yes_ask_low     numeric,
    yes_ask_close   numeric,
    volume          numeric,
    open_interest   numeric,
    PRIMARY KEY (ticker, period_interval, end_period_ts)
);

-- Resumable-backfill bookkeeping.
CREATE TABLE IF NOT EXISTS checkpoints (
    stage      text PRIMARY KEY,
    state      jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS candle_progress (
    ticker     text PRIMARY KEY,
    done       boolean NOT NULL DEFAULT false,
    n_candles  int,
    error      text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
