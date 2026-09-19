"""SQLite layer: connection, batched upserts, checkpoints."""

import json
import os
import pathlib
import sqlite3

SCHEMA_PATH = pathlib.Path(__file__).resolve().parent.parent / "schema.sql"

PRIMARY_KEYS = {
    "series": "ticker",
    "events": "event_ticker",
    "markets": "ticker",
    "trades": "trade_id",
    "candlesticks": "ticker, period_interval, end_period_ts",
}


def db_path() -> str:
    return os.environ.get("DB_PATH", "kalshi.db")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), timeout=60)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 60000")
    return conn


def apply_schema(conn):
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()


def upsert(conn, table: str, cols: list[str], rows: list[tuple],
           do_update: bool = True, commit: bool = True):
    if not rows:
        return
    pk = PRIMARY_KEYS[table]
    placeholders = ", ".join("?" for _ in cols)
    if do_update:
        pk_cols = {c.strip() for c in pk.split(",")}
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in pk_cols)
        action = f"DO UPDATE SET {updates}"
    else:
        action = "DO NOTHING"
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
           f"ON CONFLICT ({pk}) {action}")
    conn.executemany(sql, rows)
    if commit:
        conn.commit()


def get_checkpoint(conn, stage: str) -> dict:
    row = conn.execute("SELECT state FROM checkpoints WHERE stage = ?", (stage,)).fetchone()
    return json.loads(row[0]) if row else {}


def set_checkpoint(conn, stage: str, state: dict, commit: bool = True):
    conn.execute(
        """INSERT INTO checkpoints (stage, state) VALUES (?, ?)
           ON CONFLICT (stage) DO UPDATE SET state = excluded.state""",
        (stage, json.dumps(state)),
    )
    if commit:
        conn.commit()
