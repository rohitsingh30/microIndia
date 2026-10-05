"""Resumable offline backfill for reel-level derived analysis."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .intelligence import FEATURE_VERSION, REEL_ANALYSIS_VERSION, derive_post_features


DEFAULT_DATABASE = "data/microindia.sqlite3"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(database: str, busy_timeout_ms: int) -> sqlite3.Connection:
    connection = sqlite3.connect(database, timeout=busy_timeout_ms / 1000)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    return connection


def _with_busy_retry(callback: Any, retries: int, retry_seconds: float) -> Any:
    for attempt in range(retries + 1):
        try:
            return callback()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            if attempt >= retries:
                raise
            time.sleep(retry_seconds * (attempt + 1))
    raise RuntimeError("unreachable")


def _needs_analysis(payload: Dict[str, Any], feature_version: Optional[str] = None) -> bool:
    analysis = payload.get("reel_analysis")
    if feature_version is not None and feature_version != FEATURE_VERSION:
        return True
    return not isinstance(analysis, dict) or analysis.get("analysis_version") != REEL_ANALYSIS_VERSION


def backfill(
    database: str,
    *,
    batch_size: int = 25,
    max_records: Optional[int] = None,
    busy_timeout_ms: int = 5000,
    lock_retries: int = 8,
    retry_seconds: float = 0.25,
) -> Dict[str, int]:
    """Backfill missing/outdated reel features without visiting a browser."""
    connection = _connect(database, busy_timeout_ms)
    counts = {"examined": 0, "updated": 0, "skipped": 0, "low_evidence": 0}
    try:
        while max_records is None or counts["examined"] < max_records:
            remaining = None if max_records is None else max_records - counts["examined"]
            limit = batch_size if remaining is None else min(batch_size, remaining)
            rows = connection.execute(
                """SELECT p.post_id, po.payload AS observation_payload, ps.payload AS profile_payload,
                          pf.payload AS feature_payload, pf.feature_version AS feature_version
                   FROM posts p
                   JOIN post_observations po ON po.observation_id = (
                       SELECT po2.observation_id FROM post_observations po2
                       WHERE po2.post_id = p.post_id ORDER BY po2.observed_at DESC, po2.observation_id DESC LIMIT 1
                   )
                   JOIN profile_snapshots ps ON ps.capture_id = po.capture_id
                   LEFT JOIN post_features pf ON pf.post_id = p.post_id
                   WHERE p.content_type = 'reel'
                   ORDER BY p.post_id
                   LIMIT ? OFFSET ?""",
                (limit, counts["examined"]),
            ).fetchall()
            if not rows:
                break
            updates = []
            for row in rows:
                counts["examined"] += 1
                existing = json.loads(row["feature_payload"]) if row["feature_payload"] else {}
                if not _needs_analysis(existing, row["feature_version"] or ""):
                    counts["skipped"] += 1
                    continue
                observation = json.loads(row["observation_payload"])
                profile = json.loads(row["profile_payload"])
                features = derive_post_features(observation, profile.get("follower_count"))
                analysis = features.get("reel_analysis") or {}
                if analysis.get("analysis_confidence") == "insufficient_text_evidence":
                    counts["low_evidence"] += 1
                updates.append((
                    FEATURE_VERSION,
                    json.dumps(features, sort_keys=True),
                    _now(),
                    row["post_id"],
                ))
            if updates:
                def write_batch() -> None:
                    with connection:
                        connection.executemany(
                            """INSERT INTO post_features(post_id, feature_version, payload, calculated_at)
                               VALUES (?, ?, ?, ?)
                               ON CONFLICT(post_id) DO UPDATE SET feature_version=excluded.feature_version,
                               payload=excluded.payload, calculated_at=excluded.calculated_at""",
                            [(post_id, version, payload, calculated_at) for version, payload, calculated_at, post_id in updates],
                        )
                _with_busy_retry(write_batch, lock_retries, retry_seconds)
                counts["updated"] += len(updates)
            if len(rows) < limit:
                break
    finally:
        connection.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill offline reel analysis from immutable raw observations")
    parser.add_argument("--database", default=os.environ.get("MICROINDIA_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--max-records", type=int)
    parser.add_argument("--busy-timeout-ms", type=int, default=5000)
    parser.add_argument("--lock-retries", type=int, default=8)
    parser.add_argument("--retry-seconds", type=float, default=0.25)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    result = backfill(
        args.database,
        batch_size=args.batch_size,
        max_records=args.max_records,
        busy_timeout_ms=args.busy_timeout_ms,
        lock_retries=args.lock_retries,
        retry_seconds=args.retry_seconds,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()