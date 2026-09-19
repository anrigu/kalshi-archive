#!/usr/bin/env bash
# Runs ON the VM (under systemd): backfill -> export -> upload dumps to GCS.
set -euo pipefail
cd /opt/kalshi-archive

venv/bin/python -m kalshi_archive backfill
venv/bin/python -m kalshi_archive export --out dumps

BUCKET="${DUMP_BUCKET:-gs://prophet-kalshi-archive}"
gsutil -m cp dumps/kalshi-*.sql.gz* "$BUCKET/"
echo "DUMPS UPLOADED to $BUCKET"
