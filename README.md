# kalshi-archive

A one-shot backfill of everything Kalshi's public trade API exposes, into a
Supabase Postgres database. No API key required — all endpoints used are
public reads on `api.elections.kalshi.com/trade-api/v2`.

## What it captures

| Table | Source | Contents |
|---|---|---|
| `series` | `GET /series/{ticker}` | Every series referenced by an event (full raw JSON kept) |
| `events` | `GET /events` (paginated) | Every event, all statuses (full raw JSON kept) |
| `markets` | `GET /markets` (paginated) | Every market ever listed, all statuses incl. settled (full raw JSON kept) |
| `trades` | `GET /markets/trades` (global tape) | Every individual trade, newest → oldest back to the exchange's beginning |
| `candlesticks` | `GET /series/{s}/markets/{m}/candlesticks` | OHLC price + yes bid/ask + volume + open interest per market. Minute bars for markets that lived ≤2 days (Kalshi's 15-minute crypto markets etc.), hourly bars otherwise |

Bookkeeping tables `checkpoints` and `candle_progress` make every stage
resumable: kill the process at any point and rerunning continues where it
stopped (systemd restarts it automatically on the VM).

By default candlesticks are fetched only for markets with volume > 0 — a
zero-volume market's candle endpoint returns an empty array, so nothing is
lost, and it saves days of requests. Set `CANDLES_MIN_VOLUME=0` to force all.

## Run it

```bash
pip install -r requirements.txt
export DATABASE_URL='postgresql://postgres:...@db.<ref>.supabase.co:5432/postgres'
python -m kalshi_archive                  # all stages, in order
python -m kalshi_archive --stages trades  # one stage
python -m kalshi_archive --rps 5          # gentler rate limit (default 8 req/s)
```

Env knobs: `KALSHI_RPS` is `--rps`; `CANDLE_WORKERS` (default 6) parallelizes
the per-market candle fetch; `CANDLES_MIN_VOLUME` (default: volume > 0).

## Run it on GCP (the intended path)

```bash
cat > /tmp/backfill.env <<'EOF'
DATABASE_URL=postgresql://postgres:...@db.<ref>.supabase.co:5432/postgres
EOF
deploy/run_backfill_vm.sh /tmp/backfill.env
```

That creates an `e2-standard-2` VM (`kalshi-archive-backfill`, project
`gen-lang-client-0850145540`, zone `us-east5-a`), ships this repo, and starts
the backfill under systemd with restart-on-failure. Follow progress:

```bash
gcloud compute ssh kalshi-archive-backfill --project=gen-lang-client-0850145540 \
  --zone=us-east5-a --command='sudo journalctl -u kalshi-backfill -f'
```

When the log says `backfill complete`, delete the VM:

```bash
gcloud compute instances delete kalshi-archive-backfill \
  --project=gen-lang-client-0850145540 --zone=us-east5-a
```

Expect the full run to take on the order of days at 8 req/s — the trade tape
and per-market candles dominate. Use the Supabase dashboard's database size
panel to watch it grow; the trade tape is the bulk of the final size.

## Rate limits & auth

Default is 20 req/s anonymous. All endpoints used are public reads; measured
2026-09-19, anonymous traffic sustained 35 req/s with zero 429s, and the
serial pagination stages are latency-bound (~4-8 pages/s) rather than
limiter-bound anyway. Kalshi's documented token budget (Basic tier: 200
tokens/s at 10 tokens/request = 20 req/s; self-serve Advanced tier: 30 req/s)
applies to signed requests. To sign, set `KALSHI_ACCESS_KEY_ID` (the key ID
UUID from the Kalshi dashboard) plus `KALSHI_PRIVATE_KEY_PEM_B64` (base64 PEM)
or `KALSHI_PRIVATE_KEY_PATH`. Note: sending the PEM blob as the access key
(the legacy ai-prophet-web behaviour) does NOT authenticate — verified 401 on
an authenticated endpoint; a real key ID is required.

## What the API actually retains (measured 2026-09-19)

Kalshi launched July 2021, but the public v2 API serves far less than full
history, and authentication does not change data availability:

- **Trades:** ~90 days, globally and per-ticker alike. Probes: `max_ts` of
  now-60d returns trades, now-90d is empty; per-ticker trades on traded
  markets that closed in April/June 2026 return empty.
- **Markets/events/series metadata:** only ~6,700 markets closing before 2026
  are enumerable at all (remnants back to mid-2023), and every one of them
  reports zero volume. Full metadata exists for 2026 markets.
- **Candlesticks:** served wherever the market is still enumerable and traded
  (verified 81 hourly candles on an April-2026 market) — deeper than trades,
  bounded by the metadata retention above.

So the archive's real depth is: full detail for roughly the current year,
a ~90-day exact trade tape, and skeletal metadata before that. This is
everything the API exposes.

- The Supabase project should be created with enough disk headroom (the
  archive is multi-GB; Supabase Pro auto-scales disk).
- Stage order matters: `candles` needs `markets` + `events` done first (it
  joins them to find each market's series ticker); the driver runs them in
  the right order by default.
- Rerunning after completion is a cheap no-op for markets/events/trades
  (checkpoints record `done`) — to refresh instead, delete the relevant
  `checkpoints` rows.
