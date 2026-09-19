"""Smoke test: run every stage against the live API with capped pagination,
into a throwaway SQLite file, then export it.
Usage: DB_PATH=/tmp/kalshi-smoke.db python tests/smoke_test.py
"""

import logging
import sys

sys.path.insert(0, ".")

from kalshi_archive import backfill, db  # noqa: E402
from kalshi_archive.client import KalshiClient  # noqa: E402
from kalshi_archive.export import export  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

MAX_PAGES = 3

_orig_paginate = KalshiClient.paginate


def capped_paginate(self, path, params, items_key, cursor=None):
    for i, (items, cur) in enumerate(_orig_paginate(self, path, params, items_key, cursor)):
        if i + 1 >= MAX_PAGES:
            yield items, ""  # pretend the API ran out
            return
        yield items, cur


KalshiClient.paginate = capped_paginate

client = KalshiClient(rps=20)
conn = db.connect()
db.apply_schema(conn)

backfill.run_markets(client, conn)
backfill.run_events(client, conn)

# Trim series work: keep it to a handful.
conn.execute("""DELETE FROM events WHERE event_ticker NOT IN
                (SELECT event_ticker FROM events LIMIT 40)""")
conn.commit()
backfill.run_series(client, conn)

backfill.run_trades(client, conn)

# Candles: keep only a few traded markets, and make sure their events exist
# (the stage fetches missing events itself — exercise that path too).
conn.execute("""DELETE FROM markets WHERE ticker NOT IN
                (SELECT ticker FROM markets WHERE volume > 0 ORDER BY volume DESC LIMIT 8)""")
conn.commit()
backfill.run_candles(client, conn, workers=3)

for t in ["series", "events", "markets", "trades", "candlesticks", "candle_progress"]:
    print(f"{t}: {conn.execute(f'SELECT count(*) FROM {t}').fetchone()[0]} rows")
for stage, state in conn.execute("SELECT stage, state FROM checkpoints ORDER BY stage"):
    print(f"checkpoint {stage}: {state}")
errs = conn.execute("""SELECT count(*) FROM candle_progress
                       WHERE error IS NOT NULL AND error != 'not_found'""").fetchone()[0]
print(f"candle errors: {errs}")
assert errs == 0, "candle stage had errors"

parts = export("/tmp/kalshi-smoke-dumps")
assert parts, "export produced no files"

# Round-trip: restore the dump into a fresh db and compare a count.
import gzip, os, sqlite3  # noqa: E402
restored = sqlite3.connect(":memory:")
for p in parts:
    restored.executescript(gzip.open(p, "rt").read())
want = conn.execute("SELECT count(*) FROM trades").fetchone()[0]
got = restored.execute("SELECT count(*) FROM trades").fetchone()[0]
assert want == got == 3000, (want, got)
print(f"dump round-trip ok ({got} trades)")
print("SMOKE OK")
