#!/usr/bin/env bash
# Provision a GCP VM, run the one-shot Kalshi backfill on it, export the
# archive as gzipped SQL dumps, and push them into this repo.
#
# Usage: deploy/run_backfill_vm.sh
#   Optional env: KALSHI_RPS, CANDLE_WORKERS, CANDLES_MIN_VOLUME (forwarded),
#   DUMP_BUCKET (default gs://prophet-kalshi-archive) for the final upload.
#
# The backfill runs as a systemd unit (kalshi-backfill) with Restart=on-failure;
# checkpoints live in the SQLite file, so restarts resume cleanly. The unit
# logs "DUMPS UPLOADED" when everything is in the bucket — then delete the VM:
#   gcloud compute instances delete kalshi-archive-backfill --project=$PROJECT --zone=$ZONE

set -euo pipefail

PROJECT="${PROJECT:-gen-lang-client-0850145540}"
ZONE="${ZONE:-us-east5-a}"
NAME="${NAME:-kalshi-archive-backfill}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

ENV_FILE="$(mktemp -t kalshi-env)"
chmod 600 "$ENV_FILE"
{
  echo "DUMP_BUCKET=${DUMP_BUCKET:-gs://prophet-kalshi-archive}"
  for v in KALSHI_RPS CANDLE_WORKERS CANDLES_MIN_VOLUME; do
    [ -n "${!v:-}" ] && echo "$v=${!v}"
  done
} > "$ENV_FILE"

echo "==> creating VM $NAME in $PROJECT/$ZONE"
gcloud compute instances create "$NAME" \
  --project="$PROJECT" --zone="$ZONE" \
  --machine-type=e2-standard-2 \
  --scopes=default,storage-rw \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=500GB --boot-disk-type=pd-balanced

echo "==> waiting for SSH"
for i in $(seq 1 30); do
  gcloud compute ssh "$NAME" --project="$PROJECT" --zone="$ZONE" --command=true 2>/dev/null && break
  sleep 10
done

echo "==> shipping code + env"
TARBALL="$(mktemp -t kalshi-archive).tar.gz"
tar -czf "$TARBALL" -C "$REPO_ROOT" --exclude .git \
  kalshi_archive schema.sql requirements.txt deploy/vm_run.sh
gcloud compute scp "$TARBALL" "$ENV_FILE" "$NAME":/tmp/ --project="$PROJECT" --zone="$ZONE"
TAR_BASENAME="$(basename "$TARBALL")"
ENV_BASENAME="$(basename "$ENV_FILE")"
rm -f "$ENV_FILE"

echo "==> bootstrapping and starting kalshi-backfill unit"
gcloud compute ssh "$NAME" --project="$PROJECT" --zone="$ZONE" --command="
  set -euo pipefail
  sudo apt-get update -qq && sudo apt-get install -y -qq python3-venv git >/dev/null
  sudo mkdir -p /opt/kalshi-archive
  sudo tar -xzf /tmp/$TAR_BASENAME -C /opt/kalshi-archive
  sudo mv /tmp/$ENV_BASENAME /opt/kalshi-archive/backfill.env
  sudo chmod 600 /opt/kalshi-archive/backfill.env
  sudo python3 -m venv /opt/kalshi-archive/venv
  sudo /opt/kalshi-archive/venv/bin/pip install -q -r /opt/kalshi-archive/requirements.txt
  sudo chmod +x /opt/kalshi-archive/deploy/vm_run.sh
  sudo systemd-run --unit=kalshi-backfill \
    --property=Restart=on-failure --property=RestartSec=30 \
    --property=EnvironmentFile=/opt/kalshi-archive/backfill.env \
    --property=WorkingDirectory=/opt/kalshi-archive \
    /opt/kalshi-archive/deploy/vm_run.sh
  echo '--- unit started; first log lines: ---'
  sleep 5
  sudo journalctl -u kalshi-backfill -n 20 --no-pager
"

echo
echo "Backfill is running. Follow it with:"
echo "  gcloud compute ssh $NAME --project=$PROJECT --zone=$ZONE --command='sudo journalctl -u kalshi-backfill -f'"
