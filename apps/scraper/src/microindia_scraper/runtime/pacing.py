"""One shared, self-tuning pace for every Instagram request across all runners.

Additive-increase / multiplicative-decrease, the way TCP finds a safe rate:
- every request waits ``delay`` seconds (per tab) first, plus any global cooldown;
- a 429 doubles the delay and pauses everyone for COOLDOWN seconds;
- every CLEAN_WINDOW seconds without a 429 the delay eases off by 15%.
State lives in the shared SQLite file, so the sourcer and scraper slow down together.
Per-hour counts of OK and 429 responses feed the System page.
"""

from __future__ import annotations

import asyncio
import json
import random
import sqlite3
import time
from typing import Any, Dict, Optional

FLAG = "pace"
MIN_DELAY = 0.5        # seconds between requests per tab, at full speed
MAX_DELAY = 30.0
START_DELAY = 2.0
COOLDOWN = 120.0       # everyone pauses this long after a 429
CLEAN_WINDOW = 300.0   # this long without a 429 -> speed up a little


class Throttled(RuntimeError):
    """Instagram answered 429 (or its rate-limit page)."""


class Pacer:
    def __init__(self, database: str) -> None:
        self.database = database
        self._connection: Optional[sqlite3.Connection] = None

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connection = sqlite3.connect(self.database, timeout=10, check_same_thread=False)
        return self._connection

    def state(self, now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        try:
            row = self._db().execute("SELECT value FROM runtime_flags WHERE name=?", (FLAG,)).fetchone()
            state = json.loads(row[0]) if row else None
        except (sqlite3.Error, ValueError, TypeError):
            state = None
        default = {"delay": START_DELAY, "cooldown_until": 0.0, "clean_since": now, "last_429": None}
        if not isinstance(state, dict):
            return default
        # Never let a malformed state (e.g. a hand edit storing text) break requests: coerce or fall back.
        try:
            return {
                "delay": min(MAX_DELAY, max(MIN_DELAY, float(state.get("delay", START_DELAY)))),
                "cooldown_until": float(state.get("cooldown_until") or 0.0),
                "clean_since": float(state.get("clean_since") or now),
                "last_429": float(state["last_429"]) if state.get("last_429") else None,
            }
        except (TypeError, ValueError):
            return default

    def _save(self, state: Dict[str, Any]) -> None:
        try:
            self._db().execute(
                """INSERT INTO runtime_flags(name, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (FLAG, json.dumps(state), time.time()),
            )
            self._db().commit()
        except sqlite3.Error:
            pass

    def _count(self, name: str, now: float) -> None:
        hour = time.strftime("%Y-%m-%dT%H", time.localtime(now))
        try:
            self._db().execute(
                """INSERT INTO usage(day, name, count) VALUES (?, ?, 1)
                   ON CONFLICT(day, name) DO UPDATE SET count = count + 1""",
                (hour, name),
            )
            self._db().commit()
        except sqlite3.Error:
            pass

    async def before(self) -> None:
        state = self.state()
        wait = max(0.0, state["cooldown_until"] - time.time())
        await asyncio.sleep(wait + state["delay"] * random.uniform(0.7, 1.3))

    def record(self, status: int, *, now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        state = self.state(now)
        if status == 429:
            self._count("ig_429", now)
            state.update(delay=min(MAX_DELAY, max(state["delay"] * 2, 1.0)), cooldown_until=now + COOLDOWN,
                         clean_since=now, last_429=now)
            self._save(state)
        else:
            self._count("ig_ok", now)
            if now - state["clean_since"] >= CLEAN_WINDOW and state["delay"] > MIN_DELAY:
                state.update(delay=max(MIN_DELAY, round(state["delay"] * 0.85, 3)), clean_since=now)
                self._save(state)
        return state
