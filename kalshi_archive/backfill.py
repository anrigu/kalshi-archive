"""One-shot resumable backfill of the entire public Kalshi archive into SQLite.

Stages (in order): markets -> events -> series -> trades -> candles.
Every stage checkpoints into the same SQLite file, so a killed run resumes
where it left off.
"""

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import db
from .client import KalshiClient, NotFoundError

log = logging.getLogger("backfill")

CANDLE_MAX_PERIODS = 5000  # API cap per candlesticks request


def _num(v):
    if v in (None, ""):
        return None
    return float(v)


def _ts_to_epoch(iso: str | None) -> int | None:
    if not iso:
        return None
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


# ---------------------------------------------------------------- markets

# The default (no-status) sweep only walks OPEN markets forward in time —
# settled/closed/unopened are separate paginated lanes (verified 2026-09-19),
# and settled/closed is where ALL historical markets live.
MARKET_STATUS_LANES = [None, "unopened", "closed", "settled"]
EVENT_STATUS_LANES = [None, "closed", "settled"]


def run_markets(client: KalshiClient, conn):
    for status in MARKET_STATUS_LANES:
        _sweep_markets(client, conn, status)


def _sweep_markets(client: KalshiClient, conn, status: str | None):
    stage = "markets" if status is None else f"markets:{status}"
    state = db.get_checkpoint(conn, stage)
    if state.get("done"):
        log.info("%s: already complete (%s rows)", stage, state.get("count"))
        return
    cursor = state.get("cursor")
    count = state.get("count", 0)
    params = {"limit": 1000}
    if status:
        params["status"] = status
    for items, cursor in client.paginate("/markets", params, "markets", cursor):
        rows = [(
            m["ticker"], m.get("event_ticker"), m.get("market_type"), m.get("title"),
            m.get("status"), m.get("result"),
            m.get("open_time"), m.get("close_time"), m.get("expiration_time"),
            _num(m.get("volume_fp") or m.get("volume")),
            _num(m.get("open_interest_fp") or m.get("open_interest")),
            _num(m.get("liquidity_dollars")), _num(m.get("last_price_dollars")),
            json.dumps(m),
        ) for m in items]
        db.upsert(conn, "markets",
                  ["ticker", "event_ticker", "market_type", "title", "status", "result",
                   "open_time", "close_time", "expiration_time", "volume", "open_interest",
                   "liquidity_dollars", "last_price_dollars", "raw"],
                  rows, commit=False)
        count += len(rows)
        db.set_checkpoint(conn, stage, {"cursor": cursor, "count": count})
        if count % 50_000 < 1000:
            log.info("%s: %s rows", stage, count)
    db.set_checkpoint(conn, stage, {"done": True, "count": count})
    log.info("%s: complete, %s rows", stage, count)


# ---------------------------------------------------------------- events

def run_events(client: KalshiClient, conn):
    for status in EVENT_STATUS_LANES:
        _sweep_events(client, conn, status)


def _sweep_events(client: KalshiClient, conn, status: str | None):
    stage = "events" if status is None else f"events:{status}"
    state = db.get_checkpoint(conn, stage)
    if state.get("done"):
        log.info("%s: already complete (%s rows)", stage, state.get("count"))
        return
    cursor = state.get("cursor")
    count = state.get("count", 0)
    params = {"limit": 200}
    if status:
        params["status"] = status
    for items, cursor in client.paginate("/events", params, "events", cursor):
        _upsert_events(conn, items, commit=False)
        count += len(items)
        db.set_checkpoint(conn, stage, {"cursor": cursor, "count": count})
        if count % 10_000 < 200:
            log.info("%s: %s rows", stage, count)
    db.set_checkpoint(conn, stage, {"done": True, "count": count})
    log.info("%s: complete, %s rows", stage, count)


def _upsert_events(conn, items, commit=True):
    rows = [(
        e["event_ticker"], e.get("series_ticker"), e.get("title"), e.get("sub_title"),
        e.get("category"), e.get("mutually_exclusive"), json.dumps(e),
    ) for e in items]
    db.upsert(conn, "events",
              ["event_ticker", "series_ticker", "title", "sub_title", "category",
               "mutually_exclusive", "raw"],
              rows, commit=commit)


# ---------------------------------------------------------------- series

def run_series(client: KalshiClient, conn):
    missing = [r[0] for r in conn.execute(
        """SELECT DISTINCT e.series_ticker FROM events e
           LEFT JOIN series s ON s.ticker = e.series_ticker
           WHERE e.series_ticker IS NOT NULL AND s.ticker IS NULL""")]
    log.info("series: %s to fetch", len(missing))
    for i, ticker in enumerate(missing, 1):
        try:
            s = client.get(f"/series/{ticker}")["series"]
        except NotFoundError:
            log.warning("series %s: 404, skipping", ticker)
            continue
        db.upsert(conn, "series",
                  ["ticker", "title", "category", "frequency", "raw"],
                  [(s["ticker"], s.get("title"), s.get("category"), s.get("frequency"),
                    json.dumps(s))])
        if i % 500 == 0:
            log.info("series: %s/%s", i, len(missing))
    log.info("series: complete")


# ---------------------------------------------------------------- trades

def run_trades(client: KalshiClient, conn):
    state = db.get_checkpoint(conn, "trades")
    if state.get("done"):
        log.info("trades: already complete (%s rows)", state.get("count"))
        return
    cursor = state.get("cursor")
    count = state.get("count", 0)
    started = time.monotonic()
    pages_since_log = 0
    for items, cursor in client.paginate("/markets/trades", {"limit": 1000}, "trades", cursor):
        rows = [(
            t["trade_id"], t["ticker"], t["created_time"],
            _num(t.get("yes_price_dollars")), _num(t.get("no_price_dollars")),
            _num(t.get("count_fp") or t.get("count")),
            t.get("taker_side"), t.get("taker_outcome_side"), t.get("taker_book_side"),
            t.get("is_block_trade"),
        ) for t in items]
        db.upsert(conn, "trades",
                  ["trade_id", "ticker", "created_time", "yes_price_dollars",
                   "no_price_dollars", "count", "taker_side", "taker_outcome_side",
                   "taker_book_side", "is_block_trade"],
                  rows, do_update=False, commit=False)
        count += len(rows)
        db.set_checkpoint(conn, "trades", {"cursor": cursor, "count": count})
        pages_since_log += 1
        if pages_since_log >= 200:
            rate = count / (time.monotonic() - started)
            oldest = items[-1]["created_time"] if items else "?"
            log.info("trades: %s rows (%.0f/s this run), back to %s", count, rate, oldest)
            pages_since_log = 0
    db.set_checkpoint(conn, "trades", {"done": True, "count": count})
    log.info("trades: complete, %s rows", count)


# ---------------------------------------------------------------- candles

def run_candles(client: KalshiClient, conn, workers: int = 6):
    min_volume = float(os.environ.get("CANDLES_MIN_VOLUME", "0.000001"))
    # Fetch events for any markets whose event the events sweep didn't cover,
    # so the seed join below is complete.
    orphans = [r[0] for r in conn.execute(
        """SELECT DISTINCT m.event_ticker FROM markets m
           LEFT JOIN events e ON e.event_ticker = m.event_ticker
           WHERE e.event_ticker IS NULL AND m.event_ticker IS NOT NULL
             AND m.volume >= ?""", (min_volume,))]
    if orphans:
        log.info("candles: fetching %s missing events for series lookup", len(orphans))
        for et in orphans:
            try:
                _upsert_events(conn, [client.get(f"/events/{et}")["event"]])
            except NotFoundError:
                log.warning("event %s: 404", et)
    conn.execute("""INSERT INTO candle_progress (ticker)
                    SELECT ticker FROM markets WHERE volume >= ?
                    ON CONFLICT (ticker) DO NOTHING""", (min_volume,))
    conn.commit()
    pending = conn.execute(
        """SELECT m.ticker, e.series_ticker, m.open_time,
                  m.raw ->> 'created_time', m.close_time
           FROM candle_progress p
           JOIN markets m ON m.ticker = p.ticker
           JOIN events e ON e.event_ticker = m.event_ticker
           WHERE NOT p.done ORDER BY m.volume DESC""").fetchall()
    log.info("candles: %s markets pending (min_volume=%s)", len(pending), min_volume)

    done_count = 0
    count_lock = threading.Lock()

    def worker(chunk):
        nonlocal done_count
        wconn = db.connect()
        try:
            for ticker, series_ticker, open_time, created_time, close_time in chunk:
                try:
                    n = _fetch_market_candles(client, wconn, ticker, series_ticker,
                                              open_time or created_time, close_time)
                    err = None
                except NotFoundError:
                    n, err = 0, "not_found"
                except Exception as e:  # keep the sweep going; error is recorded
                    n, err = 0, repr(e)[:500]
                    log.warning("candles %s: %s", ticker, err)
                wconn.execute("""UPDATE candle_progress
                                 SET done = ?, n_candles = ?, error = ? WHERE ticker = ?""",
                              (int(err is None or err == "not_found"), n, err, ticker))
                wconn.commit()
                with count_lock:
                    done_count += 1
                    if done_count % 2000 == 0:
                        log.info("candles: %s/%s markets", done_count, len(pending))
        finally:
            wconn.close()

    chunks = [pending[i::workers] for i in range(workers)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for f in [pool.submit(worker, c) for c in chunks if c]:
            f.result()
    log.info("candles: complete, %s markets", done_count)


def _fetch_market_candles(client, conn, ticker, series_ticker, open_iso, close_iso) -> int:
    now = int(time.time())
    start = _ts_to_epoch(open_iso)
    end = min(_ts_to_epoch(close_iso) or now, now)
    if not start or end <= start:
        return 0
    # Minute candles for short-lived markets (Kalshi runs 15-minute crypto
    # markets where hourly bars would capture nothing); hourly otherwise.
    interval = 1 if (end - start) <= 2 * 86400 else 60
    step = CANDLE_MAX_PERIODS * interval * 60
    total = 0
    t = start - interval * 60
    while t < end:
        chunk_end = min(t + step, end)
        data = client.get(
            f"/series/{series_ticker}/markets/{ticker}/candlesticks",
            {"start_ts": t, "end_ts": chunk_end, "period_interval": interval})
        candles = data.get("candlesticks") or []
        rows = []
        for c in candles:
            p, yb, ya = c.get("price") or {}, c.get("yes_bid") or {}, c.get("yes_ask") or {}
            rows.append((
                ticker, interval, c["end_period_ts"],
                _num(p.get("open_dollars")), _num(p.get("high_dollars")),
                _num(p.get("low_dollars")), _num(p.get("close_dollars")),
                _num(p.get("mean_dollars")),
                _num(yb.get("open_dollars")), _num(yb.get("high_dollars")),
                _num(yb.get("low_dollars")), _num(yb.get("close_dollars")),
                _num(ya.get("open_dollars")), _num(ya.get("high_dollars")),
                _num(ya.get("low_dollars")), _num(ya.get("close_dollars")),
                _num(c.get("volume_fp") or c.get("volume")),
                _num(c.get("open_interest_fp") or c.get("open_interest")),
            ))
        db.upsert(conn, "candlesticks",
                  ["ticker", "period_interval", "end_period_ts",
                   "price_open", "price_high", "price_low", "price_close", "price_mean",
                   "yes_bid_open", "yes_bid_high", "yes_bid_low", "yes_bid_close",
                   "yes_ask_open", "yes_ask_high", "yes_ask_low", "yes_ask_close",
                   "volume", "open_interest"],
                  rows, do_update=False)
        total += len(rows)
        t = chunk_end
    return total


# ---------------------------------------------------------------- driver

ALL_STAGES = ["markets", "events", "series", "trades", "candles"]


def run(stages: list[str], rps: float):
    client = KalshiClient(rps=rps)
    conn = db.connect()
    db.apply_schema(conn)
    started = time.monotonic()
    for stage in stages:
        log.info("=== stage: %s ===", stage)
        {"markets": run_markets, "events": run_events, "series": run_series,
         "trades": run_trades,
         "candles": lambda c, x: run_candles(c, x, workers=int(os.environ.get("CANDLE_WORKERS", "6"))),
         }[stage](client, conn)
    log.info("backfill complete in %.1f min, %s API requests",
             (time.monotonic() - started) / 60, client.requests_made)
