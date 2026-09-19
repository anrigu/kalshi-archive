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

## Notes

- **History floor:** the public API does not serve markets that closed before
  roughly June 2023 (verified empirically 2026-09: `max_close_ts` probes for
  2021–May 2023 return empty; the oldest reachable close is 2023-06-22), and
  the trade tape is bounded the same way. Kalshi launched in July 2021, but
  "as far back as the API exposes" starts at that floor.

- The Supabase project should be created with enough disk headroom (the
  archive is multi-GB; Supabase Pro auto-scales disk).
- Stage order matters: `candles` needs `markets` + `events` done first (it
  joins them to find each market's series ticker); the driver runs them in
  the right order by default.
- Rerunning after completion is a cheap no-op for markets/events/trades
  (checkpoints record `done`) — to refresh instead, delete the relevant
  `checkpoints` rows.
