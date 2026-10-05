"""SQLite persistence with per-observation checkpoints and resumable captures."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

from .models import CaptureStatus, ContentObservation, ProfileCapture, ProfileObservation, jsonable

PUBLIC_VERIFICATION_TTL_SECONDS = 24 * 60 * 60
class CaptureStore:
    """Persist each successful crawl step immediately.

    The store intentionally keeps captures immutable at the observation level. A
    retry updates capture progress, but never replaces an already saved snapshot.
    """

    def __init__(self, database_path: str = "data/microindia.sqlite3") -> None:
        parent = os.path.dirname(database_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.connection = sqlite3.connect(database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 10000")
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS profile_captures (
                capture_id TEXT PRIMARY KEY,
                candidate_key TEXT NOT NULL,
                profile_url TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_content_count INTEGER NOT NULL,
                observed_content_count INTEGER NOT NULL DEFAULT 0,
                last_completed_index INTEGER NOT NULL DEFAULT 0,
                missing_fields TEXT NOT NULL DEFAULT '[]',
                warnings TEXT NOT NULL DEFAULT '[]',
                completeness_score REAL
            );

            -- Per-creator lookups (reel selection, dossiers) by URL or key, newest first.
            CREATE INDEX IF NOT EXISTS profile_captures_url ON profile_captures(profile_url, captured_at);
            CREATE INDEX IF NOT EXISTS profile_captures_key ON profile_captures(candidate_key);

            CREATE TABLE IF NOT EXISTS profile_snapshots (
                capture_id TEXT PRIMARY KEY REFERENCES profile_captures(capture_id),
                payload TEXT NOT NULL,
                observed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS content_snapshots (
                capture_id TEXT NOT NULL REFERENCES profile_captures(capture_id),
                content_index INTEGER NOT NULL,
                identity_key TEXT,
                payload TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                PRIMARY KEY (capture_id, content_index),
                UNIQUE (capture_id, identity_key)
            );

            CREATE TABLE IF NOT EXISTS metric_snapshots (
                metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
                capture_id TEXT NOT NULL REFERENCES profile_captures(capture_id),
                payload TEXT NOT NULL,
                calculated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS creators (
                creator_id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                canonical_key TEXT NOT NULL UNIQUE,
                handle TEXT,
                profile_url TEXT NOT NULL,
                lifecycle_status TEXT NOT NULL DEFAULT 'unverified',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS posts (
                post_id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_id INTEGER NOT NULL REFERENCES creators(creator_id),
                platform TEXT NOT NULL,
                canonical_key TEXT NOT NULL UNIQUE,
                permalink TEXT,
                content_type TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS post_observations (
                observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL REFERENCES posts(post_id),
                capture_id TEXT NOT NULL REFERENCES profile_captures(capture_id),
                payload TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                UNIQUE(post_id, capture_id)
            );

            CREATE TABLE IF NOT EXISTS post_features (
                post_id INTEGER PRIMARY KEY REFERENCES posts(post_id),
                feature_version TEXT NOT NULL,
                payload TEXT NOT NULL,
                calculated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS creator_features (
                creator_id INTEGER PRIMARY KEY REFERENCES creators(creator_id),
                feature_version TEXT NOT NULL,
                payload TEXT NOT NULL,
                calculated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS collection_jobs (
                job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_type TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                priority INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0,
                available_at REAL NOT NULL,
                leased_by TEXT,
                lease_until REAL,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS collection_jobs_queue
                ON collection_jobs(status, available_at, priority, job_id);


            CREATE TABLE IF NOT EXISTS collection_attempts (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL REFERENCES collection_jobs(job_id),
                worker_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                outcome TEXT,
                error_class TEXT,
                error_message TEXT
            );

            CREATE TABLE IF NOT EXISTS account_health (
                account_alias TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'healthy',
                active_worker TEXT,
                cooldown_until REAL,
                last_error TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS browser_pages (
                target_id TEXT PRIMARY KEY,
                browser_owner_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'available',
                leased_by TEXT,
                lease_until REAL,
                last_seen_at REAL NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS browser_owners (
                browser_owner_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'starting',
                cdp_url TEXT,
                minimum_pages INTEGER NOT NULL DEFAULT 1,
                page_count INTEGER NOT NULL DEFAULT 0,
                last_heartbeat_at REAL NOT NULL,
                last_error TEXT,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS profile_candidates (
                candidate_key TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                canonical_key TEXT NOT NULL,
                profile_url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'discovered',
                source_name TEXT NOT NULL,
                source_record_key TEXT NOT NULL,
                source_observed_at TEXT NOT NULL,
                public_verified_at TEXT,
                verification_expires_at REAL,
                follower_count INTEGER,
                category TEXT,
                country TEXT,
                niche TEXT,
                last_error TEXT,
                leased_by TEXT,
                lease_until REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS profile_candidates_platform_key
                ON profile_candidates(platform, canonical_key);
            CREATE UNIQUE INDEX IF NOT EXISTS profile_candidates_source_identity
                ON profile_candidates(source_name, source_record_key);
            CREATE INDEX IF NOT EXISTS profile_candidates_status_queue
                ON profile_candidates(status, updated_at, candidate_key);

            CREATE TABLE IF NOT EXISTS candidate_discoveries (
                candidate_key TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_record_key TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                explicit_public INTEGER NOT NULL,
                PRIMARY KEY (candidate_key, source_name, source_record_key, observed_at),
                FOREIGN KEY (candidate_key) REFERENCES profile_candidates(candidate_key)
            );

            CREATE TABLE IF NOT EXISTS source_cursors (
                source_name TEXT PRIMARY KEY,
                cursor TEXT NOT NULL,
                exhausted INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS source_rejections (
                rejection_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT NOT NULL,
                cursor_start TEXT NOT NULL,
                cursor_end TEXT NOT NULL,
                raw_line TEXT NOT NULL,
                reason TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                UNIQUE(source_name, cursor_start)
            );
            """
        )
        self.connection.commit()

    def enqueue_job(self, job_type: str, idempotency_key: str, payload: Dict[str, Any], *, priority: int = 0, available_at: Optional[float] = None) -> bool:
        now_value = time.time() if available_at is None else available_at
        timestamp = str(now_value)
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO collection_jobs
            (job_type, idempotency_key, payload, priority, available_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (job_type, idempotency_key, json.dumps(payload, sort_keys=True), priority, now_value, timestamp, timestamp),
        )
        self.connection.commit()
        return cursor.rowcount == 1
    def upsert_candidate(self, record: Dict[str, Any]) -> bool:
        from .candidate_source import validate_candidate_record

        candidate = validate_candidate_record(record)
        payload = candidate.as_dict()
        candidate_key = candidate.candidate_key
        now_value = time.time()
        observed_at = candidate.observed_at
        verification_expires_at = now_value + PUBLIC_VERIFICATION_TTL_SECONDS
        existing = self.connection.execute(
            "SELECT * FROM profile_candidates WHERE candidate_key = ?", (candidate_key,)
        ).fetchone()
        source_conflict = self.connection.execute(
            """SELECT candidate_key FROM profile_candidates
               WHERE source_name = ? AND source_record_key = ?""",
            (candidate.source_name, candidate.source_record_key),
        ).fetchone()
        if source_conflict is not None and source_conflict["candidate_key"] != candidate_key:
            return False
        if existing is None:
            self.connection.execute(
                """INSERT INTO profile_candidates
                   (candidate_key, platform, canonical_key, profile_url, status,
                    source_name, source_record_key, source_observed_at,
                    public_verified_at, verification_expires_at, follower_count,
                    category, country, niche, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'verified_public', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    candidate_key,
                    candidate.platform,
                    candidate.username,
                    candidate.canonical_profile_url,
                    candidate.source_name,
                    candidate.source_record_key,
                    observed_at,
                    observed_at,
                    verification_expires_at,
                    candidate.follower_count,
                    candidate.category,
                    candidate.country,
                    candidate.niche,
                    str(now_value),
                    str(now_value),
                ),
            )
        else:
            current_status = existing["status"]
            protected = {"quarantined", "captured"}
            next_status = current_status if current_status in protected else (
                "leased" if current_status == "leased" else
                "queued" if current_status == "queued" else
                "verified_public"
            )
            self.connection.execute(
                """UPDATE profile_candidates
                   SET profile_url=?, status=?, source_observed_at=?,
                       public_verified_at=COALESCE(public_verified_at, ?),
                       verification_expires_at=?,
                       follower_count=COALESCE(follower_count, ?),
                       category=COALESCE(category, ?), country=COALESCE(country, ?),
                       niche=COALESCE(niche, ?), updated_at=?
                   WHERE candidate_key=?""",
                (
                    candidate.canonical_profile_url,
                    next_status,
                    observed_at,
                    observed_at,
                    verification_expires_at,
                    candidate.follower_count,
                    candidate.category,
                    candidate.country,
                    candidate.niche,
                    str(now_value),
                    candidate_key,
                ),
            )
        self.connection.execute(
            """INSERT OR IGNORE INTO candidate_discoveries
               (candidate_key, source_name, source_record_key, observed_at,
                payload, explicit_public)
               VALUES (?, ?, ?, ?, ?, 1)""",
            (
                candidate_key,
                candidate.source_name,
                candidate.source_record_key,
                observed_at,
                json.dumps(payload, sort_keys=True),
            ),
        )
        self.connection.commit()
        return True

    def enqueue_candidate_job(self, record: Dict[str, Any], *, priority: int = 0) -> bool:
        from .candidate_source import validate_candidate_record

        candidate = validate_candidate_record(record)
        if not self.upsert_candidate(record):
            return False
        candidate_row = self.connection.execute(
            "SELECT status FROM profile_candidates WHERE candidate_key=?",
            (candidate.candidate_key,),
        ).fetchone()
        if (
            candidate_row is None
            or candidate_row["status"] in {"quarantined", "captured", "leased"}
        ):
            # A terminal or actively leased candidate is not a new capture
            # request. Refresh jobs use their own conditional transition.
            return False
        payload = candidate.as_dict()
        payload.update({
            "candidate_key": candidate.candidate_key,
            "profile_url": candidate.canonical_profile_url,
            "public_verified_at": candidate.observed_at,
        })
        added = self.enqueue_job(
            "capture_creator_profile",
            f"candidate:instagram:{candidate.username.lower()}",
            payload,
            priority=priority,
        )
        self.connection.execute(
            """UPDATE profile_candidates SET status='queued', updated_at=?
               WHERE candidate_key=? AND status='verified_public'""",
            (str(time.time()), candidate.candidate_key),
        )
        self.connection.commit()
        return added

    def _lease_candidate(self, candidate_key: str, worker_id: str, lease_seconds: int = 900) -> Optional[Dict[str, Any]]:
        now_value = time.time()
        lease_until = now_value + lease_seconds
        with self.connection:
            row = self.connection.execute(
                """SELECT * FROM profile_candidates
                   WHERE candidate_key=? AND
                     (status IN ('verified_public', 'queued') OR
                      (status='leased' AND lease_until < ?))""",
                (candidate_key, now_value),
            ).fetchone()
            if row is None:
                return None
            updated = self.connection.execute(
                """UPDATE profile_candidates
                   SET status='leased', leased_by=?, lease_until=?, updated_at=?
                   WHERE candidate_key=? AND
                     (status IN ('verified_public', 'queued') OR
                      (status='leased' AND lease_until < ?))""",
                (worker_id, lease_until, str(now_value), candidate_key, now_value),
            )
            if updated.rowcount != 1:
                return None
        result = dict(row)
        result.update(status="leased", leased_by=worker_id, lease_until=lease_until)
        return result

    def lease_candidate(self, candidate_key: str, worker_id: str, *, lease_seconds: int = 900) -> Optional[Dict[str, Any]]:
        return self._lease_candidate(candidate_key, worker_id, lease_seconds)

    def lease_verified_candidate(self, worker_id: str, lease_seconds: int = 900) -> Optional[Dict[str, Any]]:
        now_value = time.time()
        row = self.connection.execute(
            """SELECT candidate_key FROM profile_candidates
               WHERE status IN ('verified_public', 'queued') OR
                 (status='leased' AND lease_until < ?)
               ORDER BY updated_at ASC, candidate_key ASC LIMIT 1""",
            (now_value,),
        ).fetchone()
        return self._lease_candidate(row["candidate_key"], worker_id, lease_seconds) if row else None

    def mark_candidate(
        self,
        candidate_key: str,
        expected_status: str,
        status: str,
        *,
        error: Optional[str] = None,
    ) -> bool:
        if status not in {"discovered", "verified_public", "queued", "leased", "captured", "quarantined", "failed", "exhausted"}:
            raise ValueError(f"unknown candidate status: {status}")
        now_value = str(time.time())
        cursor = self.connection.execute(
            """UPDATE profile_candidates
               SET status=?, last_error=?, updated_at=?,
                   leased_by=CASE WHEN ? IN ('captured', 'quarantined', 'failed', 'exhausted') THEN NULL ELSE leased_by END,
                   lease_until=CASE WHEN ? IN ('captured', 'quarantined', 'failed', 'exhausted') THEN NULL ELSE lease_until END
               WHERE candidate_key=? AND status=?""",
            (status, error, now_value, status, status, candidate_key, expected_status),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def mark_candidate_owned(
        self,
        candidate_key: str,
        worker_id: str,
        expected_status: str,
        status: str,
        *,
        error: Optional[str] = None,
    ) -> bool:
        now_value = str(time.time())
        cursor = self.connection.execute(
            """UPDATE profile_candidates
               SET status=?, last_error=?, leased_by=NULL, lease_until=NULL, updated_at=?
               WHERE candidate_key=? AND status=? AND leased_by=?""",
            (status, error, now_value, candidate_key, expected_status, worker_id),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def save_source_cursor(self, source_name: str, cursor: str) -> None:
        self.connection.execute(
            """INSERT INTO source_cursors(source_name, cursor, exhausted, updated_at)
               VALUES (?, ?, 0, ?)
               ON CONFLICT(source_name) DO UPDATE SET cursor=excluded.cursor,
                 exhausted=0, updated_at=excluded.updated_at""",
            (source_name, str(cursor), str(time.time())),
        )
        self.connection.commit()

    def load_source_cursor(self, source_name: str) -> Optional[str]:
        row = self.connection.execute(
            "SELECT cursor FROM source_cursors WHERE source_name=?", (source_name,)
        ).fetchone()
        return str(row["cursor"]) if row else None

    def mark_source_exhausted(self, source_name: str) -> None:
        self.connection.execute(
            """INSERT INTO source_cursors(source_name, cursor, exhausted, updated_at)
               VALUES (?, COALESCE((SELECT cursor FROM source_cursors WHERE source_name=?), '0'), 1, ?)
               ON CONFLICT(source_name) DO UPDATE SET exhausted=1, updated_at=excluded.updated_at""",
            (source_name, source_name, str(time.time())),
        )
        self.connection.commit()

    def source_is_exhausted(self, source_name: str) -> bool:
        row = self.connection.execute(
            "SELECT exhausted FROM source_cursors WHERE source_name=?", (source_name,)
        ).fetchone()
        return bool(row and row["exhausted"])

    def record_source_rejection(
        self,
        source_name: str,
        cursor_start: int,
        cursor_end: int,
        raw_line: str,
        reason: str,
    ) -> None:
        self.connection.execute(
            """INSERT OR IGNORE INTO source_rejections
               (source_name, cursor_start, cursor_end, raw_line, reason, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (source_name, str(cursor_start), str(cursor_end), raw_line, reason, str(time.time())),
        )
        self.connection.commit()

    def lease_job(
        self,
        worker_id: str,
        lease_seconds: int = 900,
        *,
        shard_id: int = 0,
        shard_count: int = 1,
    ) -> Optional[Dict[str, Any]]:
        if shard_count < 1 or not 0 <= shard_id < shard_count:
            raise ValueError("invalid shard assignment")
        now_value = time.time()
        lease_until = now_value + lease_seconds
        with self.connection:
            rows = self.connection.execute(
                """SELECT * FROM collection_jobs
                   WHERE (status = 'queued' OR (status = 'leased' AND lease_until < ?))
                     AND available_at <= ?
                   ORDER BY priority DESC, job_id ASC LIMIT 2048""",
                (now_value, now_value),
            ).fetchall()
            row = None
            for candidate in rows:
                payload = json.loads(candidate["payload"])
                candidate_key = payload.get("candidate_key")
                if candidate_key and int.from_bytes(
                    hashlib.sha256(candidate_key.encode("utf-8")).digest()[:8], "big"
                ) % shard_count != shard_id:
                    continue
                row = candidate
                break
            if row is None:
                return None
            updated = self.connection.execute(
                """UPDATE collection_jobs SET status='leased', leased_by=?, lease_until=?,
                attempts=attempts+1, updated_at=? WHERE job_id=?
                AND (status='queued' OR (status='leased' AND lease_until < ?))""",
                (worker_id, lease_until, str(now_value), row["job_id"], now_value),
            )
            if updated.rowcount != 1:
                return None
        result = dict(row)
        result.update(status="leased", leased_by=worker_id, lease_until=lease_until, attempts=row["attempts"] + 1)
        result["payload"] = json.loads(result["payload"])
        return result

    def finish_job(self, job_id: int, worker_id: str, *, outcome: str = "complete", error: Optional[str] = None, retry_at: Optional[float] = None) -> bool:
        if retry_at is not None:
            status = "queued"
        elif outcome == "needs_manual_auth":
            status = "needs_manual_auth"
        elif outcome == "failed":
            status = "failed"
        else:
            status = "complete"
        now_value = str(time.time())
        cursor = self.connection.execute(
            """UPDATE collection_jobs SET status=?, available_at=COALESCE(?, available_at),
            leased_by=NULL, lease_until=NULL, last_error=?, updated_at=?
            WHERE job_id=? AND leased_by=?""",
            (status, retry_at, error, now_value, job_id, worker_id),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def start_attempt(self, job_id: int, worker_id: str, started_at: Optional[str] = None) -> int:
        timestamp = started_at or str(time.time())
        cursor = self.connection.execute(
            "INSERT INTO collection_attempts(job_id, worker_id, started_at) VALUES (?, ?, ?)",
            (job_id, worker_id, timestamp),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_attempt(
        self,
        attempt_id: int,
        *,
        outcome: str,
        error_class: Optional[str] = None,
        error_message: Optional[str] = None,
        finished_at: Optional[str] = None,
    ) -> None:
        self.connection.execute(
            """UPDATE collection_attempts SET finished_at=?, outcome=?, error_class=?, error_message=?
            WHERE attempt_id=?""",
            (finished_at or str(time.time()), outcome, error_class, error_message, attempt_id),
        )
        self.connection.commit()

    def register_browser_page(self, target_id: str, browser_owner_id: str, *, seen_at: Optional[float] = None) -> None:
        now_value = time.time() if seen_at is None else seen_at
        self.connection.execute(
            """INSERT INTO browser_pages
            (target_id, browser_owner_id, status, last_seen_at, created_at, updated_at)
            VALUES (?, ?, 'available', ?, ?, ?)
            ON CONFLICT(target_id) DO UPDATE SET
              browser_owner_id=excluded.browser_owner_id,
              last_seen_at=excluded.last_seen_at,
              updated_at=excluded.updated_at
            WHERE browser_pages.status != 'leased'""",
            (target_id, browser_owner_id, now_value, now_value, now_value),
        )
        self.connection.commit()

    def heartbeat_browser_owner(
        self,
        browser_owner_id: str,
        *,
        status: str = "healthy",
        cdp_url: Optional[str] = None,
        minimum_pages: int = 1,
        page_count: int = 0,
        last_error: Optional[str] = None,
        heartbeat_at: Optional[float] = None,
    ) -> None:
        now_value = time.time() if heartbeat_at is None else heartbeat_at
        self.connection.execute(
            """INSERT INTO browser_owners
               (browser_owner_id, status, cdp_url, minimum_pages, page_count,
                last_heartbeat_at, last_error, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(browser_owner_id) DO UPDATE SET
                 status=excluded.status, cdp_url=excluded.cdp_url,
                 minimum_pages=excluded.minimum_pages, page_count=excluded.page_count,
                 last_heartbeat_at=excluded.last_heartbeat_at,
                 last_error=excluded.last_error, updated_at=excluded.updated_at""",
            (browser_owner_id, status, cdp_url, minimum_pages, page_count, now_value, last_error, now_value),
        )
        self.connection.commit()

    def lease_browser_page(
        self,
        worker_id: str,
        *,
        target_id: Optional[str] = None,
        exclude_target_id: Optional[str] = None,
        lease_seconds: int = 900,
    ) -> Optional[Dict[str, Any]]:
        now_value = time.time()
        lease_until = now_value + lease_seconds
        conditions = ["(status = 'available' OR (status = 'leased' AND lease_until < ?))"]
        select_params: List[Any] = [now_value]
        if target_id:
            conditions.append("target_id = ?")
            select_params.append(target_id)
        if exclude_target_id:
            conditions.append("target_id != ?")
            select_params.append(exclude_target_id)
        where = " AND ".join(conditions)
        with self.connection:
            row = self.connection.execute(
                f"""SELECT * FROM browser_pages
                WHERE {where}
                ORDER BY last_seen_at ASC, target_id ASC LIMIT 1""",
                select_params,
            ).fetchone()
            if row is None:
                return None
            updated = self.connection.execute(
                """UPDATE browser_pages SET status='leased', leased_by=?, lease_until=?, updated_at=?
                WHERE target_id=? AND (status='available' OR (status='leased' AND lease_until < ?))""",
                (worker_id, lease_until, now_value, row["target_id"], now_value),
            )
            if updated.rowcount != 1:
                return None
        result = dict(row)
        result.update(status="leased", leased_by=worker_id, lease_until=lease_until)
        return result

    def renew_browser_page(self, target_id: str, worker_id: str, *, lease_seconds: int = 900) -> bool:
        now_value = time.time()
        cursor = self.connection.execute(
            """UPDATE browser_pages SET lease_until=?, updated_at=?
            WHERE target_id=? AND status='leased' AND leased_by=? AND lease_until >= ?""",
            (now_value + lease_seconds, now_value, target_id, worker_id, now_value),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def release_browser_page(self, target_id: str, worker_id: str) -> bool:
        cursor = self.connection.execute(
            """UPDATE browser_pages SET status='available', leased_by=NULL, lease_until=NULL, updated_at=?
            WHERE target_id=? AND status='leased' AND leased_by=?""",
            (time.time(), target_id, worker_id),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def mark_missing_browser_pages(self, browser_owner_id: str, active_target_ids: List[str]) -> int:
        now_value = time.time()
        if active_target_ids:
            placeholders = ",".join("?" for _ in active_target_ids)
            params: List[Any] = [now_value, browser_owner_id, *active_target_ids]
            cursor = self.connection.execute(
                f"""UPDATE browser_pages SET status='unavailable', updated_at=?
                WHERE browser_owner_id=? AND target_id NOT IN ({placeholders}) AND status != 'leased'""",
                params,
            )
        else:
            cursor = self.connection.execute(
                """UPDATE browser_pages SET status='unavailable', updated_at=?
                WHERE browser_owner_id=? AND status != 'leased'""",
                (now_value, browser_owner_id),
            )
        self.connection.commit()
        return cursor.rowcount
    def retire_browser_pages(self, browser_owner_id: str, active_target_ids: List[str]) -> int:
        """Retire page rows not owned by the current single coordinator."""
        now_value = time.time()
        if active_target_ids:
            placeholders = ",".join("?" for _ in active_target_ids)
            cursor = self.connection.execute(
                f"""UPDATE browser_pages
                    SET status='unavailable', leased_by=NULL, lease_until=NULL, updated_at=?
                    WHERE (browser_owner_id != ? OR target_id NOT IN ({placeholders}))
                      AND status != 'unavailable'""",
                (now_value, browser_owner_id, *active_target_ids),
            )
        else:
            cursor = self.connection.execute(
                """UPDATE browser_pages
                   SET status='unavailable', leased_by=NULL, lease_until=NULL, updated_at=?
                   WHERE browser_owner_id != ? AND status != 'unavailable'""",
                (now_value, browser_owner_id),
            )
        self.connection.commit()
        return cursor.rowcount

    def update_account_health(
        self,
        account_alias: str,
        *,
        status: str,
        worker_id: Optional[str] = None,
        cooldown_until: Optional[float] = None,
        last_error: Optional[str] = None,
    ) -> None:
        self.connection.execute(
            """INSERT INTO account_health(account_alias, status, active_worker, cooldown_until, last_error, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_alias) DO UPDATE SET status=excluded.status,
            active_worker=excluded.active_worker, cooldown_until=excluded.cooldown_until,
            last_error=excluded.last_error, updated_at=excluded.updated_at""",
            (account_alias, status, worker_id, cooldown_until, last_error, str(time.time())),
        )
        self.connection.commit()

    def upsert_creator_features(self, creator_id: int, features: Dict[str, Any], feature_version: str, calculated_at: str) -> None:
        self.connection.execute(
            """INSERT INTO creator_features(creator_id, feature_version, payload, calculated_at)
            VALUES (?, ?, ?, ?) ON CONFLICT(creator_id) DO UPDATE SET
            feature_version=excluded.feature_version, payload=excluded.payload, calculated_at=excluded.calculated_at""",
            (creator_id, feature_version, json.dumps(features, sort_keys=True), calculated_at),
        )
        self.connection.commit()

    def discover_creators(self, *, query: str = "", category: Optional[str] = None, language: Optional[str] = None,
                          min_followers: Optional[int] = None, max_followers: Optional[int] = None,
                          min_engagement: Optional[float] = None) -> List[Dict[str, Any]]:
        clauses = ["1=1"]
        params: List[Any] = []
        if query:
            clauses.append("(c.handle LIKE ? OR c.profile_url LIKE ? OR f.payload LIKE ?)")
            params.extend([f"%{query}%"] * 3)
        if category:
            clauses.append("json_extract(f.payload, '$.primary_category') = ?")
            params.append(category)
        if language:
            clauses.append("EXISTS (SELECT 1 FROM json_each(json_extract(f.payload, '$.languages')) WHERE value = ?)")
            params.append(language)
        if min_followers is not None:
            clauses.append("json_extract(f.payload, '$.follower_count') >= ?")
            params.append(min_followers)
        if max_followers is not None:
            clauses.append("json_extract(f.payload, '$.follower_count') <= ?")
            params.append(max_followers)
        if min_engagement is not None:
            clauses.append("json_extract(f.payload, '$.engagement_rate') >= ?")
            params.append(min_engagement)
        rows = self.connection.execute(
            f"""SELECT c.*, f.payload AS feature_payload FROM creators c
            LEFT JOIN creator_features f ON f.creator_id=c.creator_id
            WHERE {' AND '.join(clauses)} ORDER BY json_extract(f.payload, '$.engagement_rate') DESC NULLS LAST, c.updated_at DESC""",
            params,
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["features"] = json.loads(item.pop("feature_payload") or "{}")
            result.append(item)
        return result

    def create_capture(self, capture: ProfileCapture) -> None:
        self.connection.execute(
            """INSERT INTO profile_captures
            (capture_id, candidate_key, profile_url, captured_at, schema_version,
             status, requested_content_count, observed_content_count,
             last_completed_index, missing_fields, warnings, completeness_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                capture.capture_id,
                capture.candidate_key,
                capture.profile_url,
                capture.captured_at,
                capture.schema_version,
                capture.status.value,
                capture.requested_content_count,
                capture.observed_content_count,
                capture.last_completed_index,
                json.dumps(capture.missing_fields),
                json.dumps(capture.warnings),
                capture.completeness_score,
            ),
        )
        self.connection.commit()

    def save_profile_snapshot(self, observation: ProfileObservation, observed_at: str) -> bool:
        payload = json.dumps(jsonable(observation), sort_keys=True)
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO profile_snapshots (capture_id, payload, observed_at)
            VALUES (?, ?, ?)""",
            (observation.capture_id, payload, observed_at),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def save_content_snapshot(self, observation: ContentObservation, observed_at: str) -> bool:
        identity_key = observation.platform_content_id or observation.permalink
        payload = json.dumps(jsonable(observation), sort_keys=True)
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO content_snapshots
            (capture_id, content_index, identity_key, payload, observed_at)
            VALUES (?, ?, ?, ?, ?)""",
            (observation.capture_id, observation.content_index, identity_key, payload, observed_at),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def update_progress(
        self,
        capture_id: str,
        *,
        observed_content_count: int,
        last_completed_index: int,
        status: CaptureStatus = CaptureStatus.RUNNING,
        missing_fields: Optional[List[str]] = None,
        warnings: Optional[List[str]] = None,
        completeness_score: Optional[float] = None,
    ) -> None:
        self.connection.execute(
            """UPDATE profile_captures
            SET observed_content_count = ?, last_completed_index = ?, status = ?,
                missing_fields = COALESCE(?, missing_fields),
                warnings = COALESCE(?, warnings),
                completeness_score = COALESCE(?, completeness_score)
            WHERE capture_id = ?""",
            (
                observed_content_count,
                last_completed_index,
                status.value,
                json.dumps(missing_fields) if missing_fields is not None else None,
                json.dumps(warnings) if warnings is not None else None,
                completeness_score,
                capture_id,
            ),
        )
        self.connection.commit()

    def get_capture(self, capture_id: str) -> Optional[Dict]:
        row = self.connection.execute(
            "SELECT * FROM profile_captures WHERE capture_id = ?", (capture_id,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["missing_fields"] = json.loads(result["missing_fields"])
        result["warnings"] = json.loads(result["warnings"])
        return result

    def get_saved_content_indexes(self, capture_id: str) -> List[int]:
        rows = self.connection.execute(
            "SELECT content_index FROM content_snapshots WHERE capture_id = ? ORDER BY content_index",
            (capture_id,),
        ).fetchall()
        return [row["content_index"] for row in rows]

    def get_profile_payload(self, capture_id: str) -> Optional[Dict]:
        row = self.connection.execute(
            "SELECT payload FROM profile_snapshots WHERE capture_id = ?", (capture_id,)
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def get_content_payloads(self, capture_id: str) -> List[Dict]:
        rows = self.connection.execute(
            """SELECT payload FROM content_snapshots
            WHERE capture_id = ? ORDER BY content_index""",
            (capture_id,),
        ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def materialize_capture_features(self, capture_id: str, feature_version: str, calculated_at: str) -> Optional[int]:
        """Normalize one immutable capture into creator/post feature records.

        Rows are stamped with ``intelligence.FEATURE_VERSION`` (the version of the code that
        derived them). ``feature_version`` is kept for callers and ignored, so a stale label
        can never be written next to features computed by newer code.
        """
        from .intelligence import FEATURE_VERSION, derive_creator_features, derive_post_features

        feature_version = FEATURE_VERSION

        capture = self.get_capture(capture_id)
        profile = self.get_profile_payload(capture_id)
        if capture is None or profile is None:
            return None
        canonical_key = f"instagram:{(profile.get('handle') or capture['candidate_key']).lower().lstrip('@')}"
        now_value = calculated_at
        self.connection.execute(
            """INSERT INTO creators(platform, canonical_key, handle, profile_url, created_at, updated_at)
            VALUES ('instagram', ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_key) DO UPDATE SET handle=excluded.handle,
            profile_url=excluded.profile_url, updated_at=excluded.updated_at""",
            (canonical_key, profile.get("handle"), capture["profile_url"], now_value, now_value),
        )
        creator = self.connection.execute("SELECT creator_id FROM creators WHERE canonical_key = ?", (canonical_key,)).fetchone()
        creator_id = int(creator["creator_id"])
        content = self.get_content_payloads(capture_id)
        for item in content:
            identity = item.get("platform_content_id") or item.get("permalink")
            if not identity:
                continue
            post_key = f"instagram:{identity}"
            self.connection.execute(
                """INSERT INTO posts(creator_id, platform, canonical_key, permalink, content_type, first_seen_at, last_seen_at)
                VALUES (?, 'instagram', ?, ?, ?, ?, ?)
                ON CONFLICT(canonical_key) DO UPDATE SET last_seen_at=excluded.last_seen_at,
                permalink=excluded.permalink, content_type=excluded.content_type""",
                (creator_id, post_key, item.get("permalink"), item.get("content_type"), calculated_at, calculated_at),
            )
            post = self.connection.execute("SELECT post_id FROM posts WHERE canonical_key = ?", (post_key,)).fetchone()
            post_id = int(post["post_id"])
            self.connection.execute(
                "INSERT OR IGNORE INTO post_observations(post_id, capture_id, payload, observed_at) VALUES (?, ?, ?, ?)",
                (post_id, capture_id, json.dumps(item, sort_keys=True), calculated_at),
            )
            features = derive_post_features(item, profile.get("follower_count"))
            self.connection.execute(
                """INSERT INTO post_features(post_id, feature_version, payload, calculated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(post_id) DO UPDATE SET feature_version=excluded.feature_version,
                payload=excluded.payload, calculated_at=excluded.calculated_at""",
                (post_id, feature_version, json.dumps(features, sort_keys=True), calculated_at),
            )
        metrics_row = self.connection.execute(
            "SELECT payload FROM metric_snapshots WHERE capture_id = ? ORDER BY metric_id DESC LIMIT 1", (capture_id,)
        ).fetchone()
        metrics = json.loads(metrics_row["payload"]) if metrics_row else {}
        features = derive_creator_features(profile, content, metrics)
        self.connection.execute(
            """INSERT INTO creator_features(creator_id, feature_version, payload, calculated_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(creator_id) DO UPDATE SET feature_version=excluded.feature_version,
            payload=excluded.payload, calculated_at=excluded.calculated_at""",
            (creator_id, feature_version, json.dumps(features, sort_keys=True), calculated_at),
        )
        self.connection.commit()
        return creator_id

    def save_metrics(self, capture_id: str, metrics: Dict, calculated_at: str) -> None:
        self.connection.execute(
            "INSERT INTO metric_snapshots (capture_id, payload, calculated_at) VALUES (?, ?, ?)",
            (capture_id, json.dumps(metrics, sort_keys=True), calculated_at),
        )
        self.connection.commit()