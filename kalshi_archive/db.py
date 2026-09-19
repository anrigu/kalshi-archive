"""Postgres layer: connection, batched upserts, checkpoints."""

import json
import os
import pathlib

import psycopg2
import psycopg2.extras

SCHEMA_PATH = pathlib.Path(__file__).resolve().parent.parent / "schema.sql"


def connect():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is required")
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    return conn


def apply_schema(conn):
    with conn.cursor() as cur:
        cur.execute(SCHEMA_PATH.read_text())
    conn.commit()


def upsert(conn, table: str, cols: list[str], rows: list[tuple], conflict_col: str,
           do_update: bool = True, commit: bool = True):
    if not rows:
        return
    col_list = ", ".join(cols)
    if do_update:
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != conflict_col)
        action = f"DO UPDATE SET {updates}"
    else:
        action = "DO NOTHING"
    sql = (f"INSERT INTO {table} ({col_list}) VALUES %s "
           f"ON CONFLICT ({conflict_col}) {action}")
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, page_size=1000)
    if commit:
        conn.commit()


def get_checkpoint(conn, stage: str) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT state FROM checkpoints WHERE stage = %s", (stage,))
        row = cur.fetchone()
    return row[0] if row else {}


def set_checkpoint(conn, stage: str, state: dict, commit: bool = True):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO checkpoints (stage, state, updated_at) VALUES (%s, %s, now())
               ON CONFLICT (stage) DO UPDATE SET state = EXCLUDED.state, updated_at = now()""",
            (stage, json.dumps(state)),
        )
    if commit:
        conn.commit()
