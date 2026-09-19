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
cd repo
git config user.name kalshi-archive-vm
git config user.email arenaprophet@gmail.com
# One commit + push per part: GitHub rejects packs over 2GB.
for f in ../dumps/kalshi-*.sql.gz*; do
  cp "$f" dumps/
  git add dumps
  git commit -m "Kalshi archive dump $(date -u +%F): $(basename "$f")"
  git push
done
echo "DUMPS PUSHED"
