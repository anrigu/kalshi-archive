#!/usr/bin/env bash
# Provision a GCP VM and start the one-shot Kalshi backfill on it.
#
# Usage: deploy/run_backfill_vm.sh path/to/env-file
#   The env file must contain DATABASE_URL=postgresql://... (the Supabase
#   connection string) and may set KALSHI_RPS / CANDLE_WORKERS / CANDLES_MIN_VOLUME.
#
# The backfill runs as a systemd unit (kalshi-backfill) with Restart=on-failure;
# progress checkpoints live in the database itself, so restarts resume cleanly.
# When it logs "backfill complete", delete the VM:
#   gcloud compute instances delete kalshi-archive-backfill --project=$PROJECT --zone=$ZONE

set -euo pipefail

ENV_FILE="${1:?usage: run_backfill_vm.sh path/to/env-file}"
PROJECT="${PROJECT:-gen-lang-client-0850145540}"
ZONE="${ZONE:-us-east5-a}"
NAME="${NAME:-kalshi-archive-backfill}"

grep -q '^DATABASE_URL=' "$ENV_FILE" || { echo "env file must set DATABASE_URL"; exit 1; }

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> creating VM $NAME in $PROJECT/$ZONE"
gcloud compute instances create "$NAME" \
  --project="$PROJECT" --zone="$ZONE" \
  --machine-type=e2-standard-2 \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=30GB --boot-disk-type=pd-balanced

echo "==> waiting for SSH"
for i in $(seq 1 30); do
  gcloud compute ssh "$NAME" --project="$PROJECT" --zone="$ZONE" --command=true 2>/dev/null && break
  sleep 10
done

echo "==> shipping code + env"
TARBALL="$(mktemp -t kalshi-archive).tar.gz"
tar -czf "$TARBALL" -C "$REPO_ROOT" --exclude .git kalshi_archive schema.sql requirements.txt
gcloud compute scp "$TARBALL" "$ENV_FILE" "$NAME":/tmp/ --project="$PROJECT" --zone="$ZONE"
ENV_BASENAME="$(basename "$ENV_FILE")"
TAR_BASENAME="$(basename "$TARBALL")"

echo "==> bootstrapping and starting kalshi-backfill unit"
gcloud compute ssh "$NAME" --project="$PROJECT" --zone="$ZONE" --command="
  set -euo pipefail
  sudo apt-get update -qq && sudo apt-get install -y -qq python3-venv >/dev/null
  sudo mkdir -p /opt/kalshi-archive
  sudo tar -xzf /tmp/$TAR_BASENAME -C /opt/kalshi-archive
  sudo mv /tmp/$ENV_BASENAME /opt/kalshi-archive/backfill.env
  sudo chmod 600 /opt/kalshi-archive/backfill.env
  sudo python3 -m venv /opt/kalshi-archive/venv
  sudo /opt/kalshi-archive/venv/bin/pip install -q -r /opt/kalshi-archive/requirements.txt
  sudo systemd-run --unit=kalshi-backfill \
    --property=Restart=on-failure --property=RestartSec=30 \
    --property=EnvironmentFile=/opt/kalshi-archive/backfill.env \
    --property=WorkingDirectory=/opt/kalshi-archive \
    /opt/kalshi-archive/venv/bin/python -m kalshi_archive
  echo '--- unit started; first log lines: ---'
  sleep 5
  sudo journalctl -u kalshi-backfill -n 20 --no-pager
"

echo
echo "Backfill is running. Follow it with:"
echo "  gcloud compute ssh $NAME --project=$PROJECT --zone=$ZONE --command='sudo journalctl -u kalshi-backfill -f'"
