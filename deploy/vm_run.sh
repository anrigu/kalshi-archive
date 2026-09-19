#!/usr/bin/env bash
# Runs ON the VM (under systemd): backfill -> export -> push dumps to GitHub.
set -euo pipefail
cd /opt/kalshi-archive

venv/bin/python -m kalshi_archive backfill
venv/bin/python -m kalshi_archive export --out dumps

if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "no GITHUB_TOKEN; dumps left in /opt/kalshi-archive/dumps"
  exit 0
fi
rm -rf repo
git clone --depth 1 "https://x-access-token:${GITHUB_TOKEN}@github.com/anrigu/kalshi-archive.git" repo
mkdir -p repo/dumps
cp dumps/kalshi-*.sql.gz* repo/dumps/
cd repo
git -c user.name=kalshi-archive-vm -c user.email=arenaprophet@gmail.com \
  add dumps && git -c user.name=kalshi-archive-vm -c user.email=arenaprophet@gmail.com \
  commit -m "Add Kalshi archive dump $(date -u +%F)"
git push
echo "DUMPS PUSHED"
