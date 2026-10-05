"""One SQLite task table for every kind of browser work.

A task is ``(kind, key)`` unique, so re-discovering a creator never creates a
second row. ``state`` is the only state machine:

    queued -> leased -> done | skipped | failed
                     -> queued (retry, run_at in the future)
                     -> auth_blocked -> queued (when the session is healthy again)

Expired leases are re-leased; a task that keeps killing its runner stops at
``max_attempts``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional

from .results import AuthBlocked, Done, Fail, FollowUp, Result, Retry, Skip

TERMINAL_STATES = ("done", "skipped", "failed")
AUTH_FLAG = "auth_blocked"


def backoff_seconds(attempts: int) -> float:
    return float(min(3600, 30 * 2 ** max(0, attempts - 1)))


class TaskStore:
    def __init__(self, database_path: str, *, max_attempts: int = 3) -> None:
        parent = os.path.dirname(database_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.max_attempts = max_attempts
        self.connection = sqlite3.connect(database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 10000")
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL,
                key TEXT NOT NULL,
                payload TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0,
                run_at REAL NOT NULL,
                leased_by TEXT,
                lease_until REAL,
                last_error TEXT,
                result TEXT,
                parent_task_id INTEGER,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                finished_at REAL,
                UNIQUE(kind, key)
            );
            CREATE INDEX IF NOT EXISTS tasks_ready
                ON tasks(state, kind, priority DESC, run_at, task_id);

            CREATE TABLE IF NOT EXISTS runtime_flags (
                name TEXT PRIMARY KEY,
                value TEXT,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS metrics (
                ts REAL NOT NULL,
                name TEXT NOT NULL,
                value REAL,
                PRIMARY KEY (ts, name)
            );
            CREATE TABLE IF NOT EXISTS usage (
                day TEXT NOT NULL,
                name TEXT NOT NULL,
                count REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (day, name)
            );

            CREATE TABLE IF NOT EXISTS runners (
                runner_id TEXT PRIMARY KEY,
                kinds TEXT NOT NULL,
                tabs INTEGER NOT NULL,
                status TEXT NOT NULL,
                processed INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                started_at REAL NOT NULL,
                last_heartbeat REAL NOT NULL
            );
            """
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(tasks)")}
        if "duration" not in columns:  # seconds a task took (speed stats); added after first release
            self.connection.execute("ALTER TABLE tasks ADD COLUMN duration REAL")
        self.connection.commit()

    def count_usage(self, name: str, amount: float = 1.0) -> None:
        """Daily counters for cost accounting (LLM calls, tokens, ...)."""
        import datetime as _dt

        day = _dt.date.today().isoformat()
        self.connection.execute(
            """INSERT INTO usage(day, name, count) VALUES (?, ?, ?)
               ON CONFLICT(day, name) DO UPDATE SET count = count + excluded.count""",
            (day, name, amount),
        )
        self.connection.commit()

    # -- queueing ---------------------------------------------------------

    def enqueue(
        self,
        kind: str,
        key: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        priority: int = 0,
        delay_seconds: float = 0.0,
        parent_task_id: Optional[int] = None,
        revisit: bool = False,
        now: Optional[float] = None,
    ) -> bool:
        """Queue a task. Returns True when a row was added (or revived with ``revisit``)."""
        if not kind or not key:
            raise ValueError("tasks need a kind and a key")
        current = time.time() if now is None else now
        conflict = (
            """DO UPDATE SET state='queued', run_at=excluded.run_at, attempts=0,
                 leased_by=NULL, lease_until=NULL, payload=excluded.payload,
                 priority=MAX(tasks.priority, excluded.priority), updated_at=excluded.updated_at
               WHERE tasks.state IN ('done', 'skipped', 'failed')"""
            if revisit
            else "DO NOTHING"
        )
        cursor = self.connection.execute(
            f"""INSERT INTO tasks(kind, key, payload, priority, state, run_at, parent_task_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?)
                ON CONFLICT(kind, key) {conflict}""",
            (kind, key, json.dumps(payload or {}, sort_keys=True), priority,
             current + delay_seconds, parent_task_id, current, current),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def enqueue_follow_ups(self, follow_ups: Iterable[FollowUp], *, parent_task_id: Optional[int] = None) -> int:
        return sum(
            self.enqueue(
                item.kind,
                item.key,
                item.payload,
                priority=item.priority,
                delay_seconds=item.delay_seconds,
                parent_task_id=parent_task_id,
            )
            for item in follow_ups
        )

    # -- leasing ----------------------------------------------------------

    def lease(
        self,
        runner_id: str,
        kinds: List[str],
        *,
        lease_seconds: float = 900.0,
        now: Optional[float] = None,
        priority_floor: Optional[Dict[str, int]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Lease the best due task of these kinds. ``priority_floor`` holds back low-priority work
        of a kind (backpressure) while still letting its urgent tasks through."""
        if not kinds:
            return None
        current = time.time() if now is None else now
        marks = ",".join("?" for _ in kinds)
        # A task whose lease expired at the attempt cap has crashed its runner
        # too often; stop handing it out.
        self.connection.execute(
            f"""UPDATE tasks SET state='failed', leased_by=NULL, lease_until=NULL,
                  last_error='lease expired at attempt cap; last error: ' || COALESCE(last_error, 'none'),
                  finished_at=?, updated_at=?
                WHERE kind IN ({marks}) AND state='leased' AND lease_until < ? AND attempts >= ?""",
            (current, current, *kinds, current, self.max_attempts),
        )
        floors = priority_floor or {}
        floor_sql = "".join(" AND NOT (kind = ? AND priority < ?)" for _ in floors)
        floor_args = [value for kind, floor in floors.items() for value in (kind, floor)]
        row = self.connection.execute(
            f"""UPDATE tasks
                SET state='leased', leased_by=?, lease_until=?, attempts=attempts+1, updated_at=?
                WHERE task_id = (
                    SELECT task_id FROM tasks
                    WHERE kind IN ({marks})
                      AND ((state='queued' AND run_at <= ?) OR (state='leased' AND lease_until < ?))
                      {floor_sql}
                    ORDER BY priority DESC, run_at ASC, task_id ASC
                    LIMIT 1
                )
                RETURNING *""",
            (runner_id, current + lease_seconds, current, *kinds, current, current, *floor_args),
        ).fetchone()
        self.connection.commit()
        if row is None:
            return None
        task = dict(row)
        task["payload"] = json.loads(task["payload"])
        return task

    def release_leases(self, runner_id: str) -> int:
        """Requeue whatever a previous run of this runner left leased (crash/restart)."""
        current = time.time()
        cursor = self.connection.execute(
            """UPDATE tasks SET state='queued', run_at=?, leased_by=NULL, lease_until=NULL, updated_at=?
               WHERE state='leased' AND leased_by=?""",
            (current, current, runner_id),
        )
        self.connection.commit()
        return cursor.rowcount

    def renew(self, task: Dict[str, Any], runner_id: str, *, lease_seconds: float = 900.0) -> bool:
        current = time.time()
        cursor = self.connection.execute(
            "UPDATE tasks SET lease_until=?, updated_at=? WHERE task_id=? AND state='leased' AND leased_by=?",
            (current + lease_seconds, current, task["task_id"], runner_id),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    # -- results ----------------------------------------------------------

    def apply(self, task: Dict[str, Any], runner_id: str, result: Result, *, now: Optional[float] = None,
              duration: Optional[float] = None) -> str:
        """Record a handler result. Returns the task's new state ('lost' if the lease was taken)."""
        current = time.time() if now is None else now
        attempts = int(task.get("attempts") or 0)
        follow_ups: List[FollowUp] = []
        auth_reason: Optional[str] = None
        if isinstance(result, Done):
            state, error, data = "done", None, result.data
            follow_ups = result.follow_ups
            run_at = None
        elif isinstance(result, Skip):
            state, error, data, run_at = "skipped", result.reason, result.data, None
        elif isinstance(result, Fail):
            state, error, data, run_at = "failed", result.reason, None, None
        elif isinstance(result, AuthBlocked):
            # Not the task's fault: give the attempt back.
            state, error, data, run_at = "auth_blocked", result.reason, None, None
            attempts = max(0, attempts - 1)
            auth_reason = result.reason
        elif isinstance(result, Retry):
            data = None
            error = result.reason
            if attempts >= self.max_attempts:
                state, run_at = "failed", None
                error = f"gave up after {attempts} attempts: {result.reason}"
            else:
                state = "queued"
                delay = result.after_seconds if result.after_seconds is not None else backoff_seconds(attempts)
                run_at = current + delay
        else:
            raise TypeError(f"handler returned {type(result).__name__}, not a Result")
        finished = current if state in TERMINAL_STATES else None
        cursor = self.connection.execute(
            """UPDATE tasks SET state=?, attempts=?, last_error=?, result=COALESCE(?, result),
                 run_at=COALESCE(?, run_at), leased_by=NULL, lease_until=NULL,
                 finished_at=COALESCE(?, finished_at), updated_at=?, duration=COALESCE(?, duration)
               WHERE task_id=? AND state='leased' AND leased_by=?""",
            (state, attempts, error, json.dumps(data, sort_keys=True) if data is not None else None,
             run_at, finished, current, duration, task["task_id"], runner_id),
        )
        self.connection.commit()
        if cursor.rowcount != 1:
            return "lost"
        if follow_ups:
            self.enqueue_follow_ups(follow_ups, parent_task_id=task["task_id"])
        if auth_reason:
            self.set_auth_blocked(auth_reason)
        return state

    # -- session-wide pause -------------------------------------------------

    def set_auth_blocked(self, reason: str) -> None:
        self.connection.execute(
            """INSERT INTO runtime_flags(name, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (AUTH_FLAG, reason, time.time()),
        )
        self.connection.commit()

    def auth_blocked(self) -> Optional[str]:
        row = self.connection.execute(
            "SELECT value FROM runtime_flags WHERE name=?", (AUTH_FLAG,)
        ).fetchone()
        return str(row["value"]) if row else None

    def clear_auth_blocked(self) -> int:
        """Lift the pause and requeue everything parked by it. Returns tasks requeued."""
        current = time.time()
        self.connection.execute("DELETE FROM runtime_flags WHERE name=?", (AUTH_FLAG,))
        cursor = self.connection.execute(
            "UPDATE tasks SET state='queued', run_at=?, updated_at=? WHERE state='auth_blocked'",
            (current, current),
        )
        self.connection.commit()
        return cursor.rowcount

    # -- scheduling ---------------------------------------------------------

    def revisit_done(self, kind: str, *, older_than_seconds: float, where_json: str = "", limit: int = 100) -> int:
        """Requeue finished tasks of ``kind`` older than a cutoff, e.g. refreshing eligible creators.

        ``where_json`` is an extra SQL condition over ``result``, such as
        ``json_extract(result, '$.eligible') = 1``.
        """
        current = time.time()
        extra = f"AND ({where_json})" if where_json else ""
        cursor = self.connection.execute(
            f"""UPDATE tasks SET state='queued', run_at=?, attempts=0, updated_at=?
                WHERE task_id IN (
                    SELECT task_id FROM tasks
                    WHERE kind=? AND state='done' AND finished_at < ? {extra}
                    ORDER BY finished_at ASC LIMIT ?
                )""",
            (current, current, kind, current - older_than_seconds, limit),
        )
        self.connection.commit()
        return cursor.rowcount

    def pending(self, kinds: List[str]) -> int:
        if not kinds:
            return 0
        marks = ",".join("?" for _ in kinds)
        row = self.connection.execute(
            f"SELECT COUNT(*) AS n FROM tasks WHERE kind IN ({marks}) AND state IN ('queued', 'leased')",
            kinds,
        ).fetchone()
        return int(row["n"])

    # -- observability --------------------------------------------------------

    def counts(self) -> Dict[str, Dict[str, int]]:
        result: Dict[str, Dict[str, int]] = {}
        for row in self.connection.execute(
            "SELECT kind, state, COUNT(*) AS n FROM tasks GROUP BY kind, state ORDER BY kind, state"
        ):
            result.setdefault(row["kind"], {})[row["state"]] = int(row["n"])
        return result

    def heartbeat(self, runner_id: str, *, kinds: List[str], tabs: int, status: str,
                  processed: int = 0, last_error: Optional[str] = None) -> None:
        current = time.time()
        self.connection.execute(
            """INSERT INTO runners(runner_id, kinds, tabs, status, processed, last_error, started_at, last_heartbeat)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(runner_id) DO UPDATE SET kinds=excluded.kinds, tabs=excluded.tabs,
                 status=excluded.status, processed=excluded.processed,
                 last_error=COALESCE(excluded.last_error, runners.last_error),
                 last_heartbeat=excluded.last_heartbeat""",
            (runner_id, ",".join(kinds), tabs, status, processed, last_error, current, current),
        )
        self.connection.commit()
