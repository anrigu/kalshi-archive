"""Export the SQLite archive as gzipped SQL dump files.

Each part is a complete gzip stream split at statement boundaries, so
`cat kalshi-*.sql.gz* | gunzip | sqlite3 restored.db` rebuilds the database
regardless of how many parts there are.
"""

import gzip
import logging
import os
from datetime import date

from . import db

log = logging.getLogger("export")

MAX_PART_BYTES = 1_800_000_000  # stay under GitHub's 2GB per-file hard limit


def export(out_dir: str):
    conn = db.connect()
    os.makedirs(out_dir, exist_ok=True)
    stem = f"kalshi-{date.today().isoformat()}.sql.gz"
    part, raw, gz = 0, None, None
    parts = []

    def roll():
        nonlocal part, raw, gz
        if gz:
            gz.close()
            raw.close()
        part += 1
        name = stem if part == 1 else f"{stem}.part{part:02d}"
        parts.append(os.path.join(out_dir, name))
        raw = open(parts[-1], "wb")
        gz = gzip.GzipFile(fileobj=raw, mode="wb", mtime=0)

    roll()
    n = 0
    for line in conn.iterdump():
        gz.write((line + "\n").encode())
        n += 1
        if n % 500_000 == 0:
            log.info("export: %s statements written", n)
        if raw.tell() > MAX_PART_BYTES:
            roll()
    gz.close()
    raw.close()

    # A single part keeps the plain name; multiple parts all get numbered.
    if len(parts) > 1:
        first_renamed = parts[0] + ".part01"
        os.rename(parts[0], first_renamed)
        parts[0] = first_renamed
    for p in parts:
        log.info("export: %s (%.1f MB)", p, os.path.getsize(p) / 1e6)
    log.info("export complete: %s statements, %s part(s)", n, len(parts))
    return parts
