"""Build the read-only snapshot the hosted dashboard serves.

    python3 deploy/make_snapshot.py [--source apps/scraper/data/microindia.sqlite3] [--out deploy/out]

Copies the live database with SQLite's backup API (safe while workers write), drops tables the
dashboard never reads (model cache, legacy collection tables), strips raw page text, raw Instagram
API responses and raw model outputs, vacuums, and gzips the result to
deploy/out/microindia-snapshot.sqlite3.gz. deploy/push.py uploads it to Nikamma.
"""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import sqlite3
import time

DROP = (
    "llm_cache",  # raw model envelopes, large and internal
    "collection_jobs", "collection_attempts", "account_health", "browser_pages", "browser_owners",
    "profile_candidates", "candidate_discoveries", "source_cursors", "source_rejections",  # legacy
)


def build(source: str, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    raw = os.path.join(out_dir, "microindia-snapshot.sqlite3")
    if os.path.exists(raw):
        os.remove(raw)
    src = sqlite3.connect(source, timeout=30)  # read only via the backup API; works with or without live writers
    dst = sqlite3.connect(raw)
    try:
        src.backup(dst)
    finally:
        src.close()
    for table in DROP:
        dst.execute(f"DROP TABLE IF EXISTS {table}")
    # Strip what the dashboard never shows: raw page text (it contains other people's comments),
    # raw Instagram API responses and raw model envelopes. The parsed fields the site uses stay.
    tables = {row[0] for row in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ("content_snapshots", "post_observations"):
        if table in tables:
            dst.execute(f"UPDATE {table} SET payload = json_remove(payload, '$.text_content') "
                        f"WHERE json_extract(payload, '$.text_content') IS NOT NULL")
    for table, column in (("reel_media", "raw_json"), ("reel_analyses", "raw"), ("creator_dossiers", "raw")):
        if table in tables:
            dst.execute(f"UPDATE {table} SET {column} = NULL")
    dst.execute("INSERT OR REPLACE INTO runtime_flags(name, value, updated_at) VALUES ('snapshot_at', ?, ?)",
                (time.strftime("%Y-%m-%dT%H:%M:%S%z"), time.time()))
    dst.commit()
    dst.execute("VACUUM")
    dst.execute("PRAGMA journal_mode=DELETE")
    dst.close()
    gz = raw + ".gz"
    with open(raw, "rb") as handle, gzip.open(gz, "wb", compresslevel=6) as packed:
        shutil.copyfileobj(handle, packed)
    os.remove(raw)
    print(f"{gz} {os.path.getsize(gz) / 1_048_576:.1f} MB")
    return gz


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="apps/scraper/data/microindia.sqlite3")
    parser.add_argument("--out", default="deploy/out")
    args = parser.parse_args()
    build(args.source, args.out)
