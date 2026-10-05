"""Daily online backup of the live database, keeping the last few days.

    python -m microindia_scraper.backup              # run forever: one backup per day
    python -m microindia_scraper.backup --once       # one backup now

Uses SQLite's online backup API, so the workers keep writing while it copies.
Backups land in data/backups/microindia-YYYY-MM-DD.sqlite3.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import time
from typing import List, Optional

DEFAULT_DATABASE = os.environ.get("MICROINDIA_DATABASE", "data/microindia.sqlite3")
PREFIX = "microindia-"


def log(**event) -> None:
    print(json.dumps({"ts": round(time.time(), 3), **event}, sort_keys=True), flush=True)


def backup_path(directory: str, day: str) -> str:
    return os.path.join(directory, f"{PREFIX}{day}.sqlite3")


def daily_backups(directory: str) -> List[str]:
    """Dated daily backups only, oldest first (one-off backups with other names are never pruned)."""
    paths = glob.glob(os.path.join(directory, f"{PREFIX}????-??-??.sqlite3"))
    return sorted(paths)


def backup_once(database: str, directory: str, *, day: Optional[str] = None, keep: int = 7) -> str:
    day = day or time.strftime("%Y-%m-%d")
    os.makedirs(directory, exist_ok=True)
    target = backup_path(directory, day)
    partial = target + ".partial"
    started = time.time()
    source = sqlite3.connect(f"file:{os.path.abspath(database)}?mode=ro", uri=True, timeout=30)
    destination = sqlite3.connect(partial)
    try:
        # One step: a stepped copy restarts whenever another connection commits between steps,
        # which under a busy writer never finishes. In WAL mode one read transaction doesn't block
        # the writers, so copying in one go is both safe and fast.
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    check = sqlite3.connect(partial)
    try:
        ok = check.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        check.close()
    if not ok:
        os.remove(partial)
        raise RuntimeError(f"backup of {database} failed quick_check")
    os.replace(partial, target)
    removed = []
    for old in daily_backups(directory)[:-keep] if keep > 0 else []:
        os.remove(old)
        removed.append(os.path.basename(old))
    log(event="backup_done", path=target, mb=round(os.path.getsize(target) / 1_048_576, 1),
        seconds=round(time.time() - started, 1), pruned=removed)
    return target


def run_forever(database: str, directory: str, *, keep: int, check_seconds: float = 600.0) -> None:
    while True:
        today = time.strftime("%Y-%m-%d")
        if not os.path.exists(backup_path(directory, today)):
            try:
                backup_once(database, directory, day=today, keep=keep)
            except Exception as exc:
                log(event="backup_failed", error=repr(exc))
        time.sleep(check_seconds)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="daily online backup of the microIndia database")
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--dir", default="data/backups")
    parser.add_argument("--keep", type=int, default=7)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if args.once:
        backup_once(args.database, args.dir, keep=args.keep)
    else:
        run_forever(args.database, args.dir, keep=args.keep)


if __name__ == "__main__":
    main()
