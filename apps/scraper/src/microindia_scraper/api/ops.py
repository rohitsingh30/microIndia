"""Live-operations queries and routes: the /ops pages' summary, charts, activity feed, pipeline,
system stats, sources, the SSE stream and the unblock/retry/seed actions."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from ..runtime.tasks import TaskStore
from .repository import _json, _source_type
from .router import Request, Stream, route

REASON_LABELS = (
    ("outside 10K-100K", "Outside follower range"),
    ("follower band", "Outside follower range"),
    ("follower range", "Outside follower range"),
    ("AI-generated", "AI-generated persona"),
    ("repost/aggregator", "Repost / aggregator page"),
    ("no India", "No India signal"),
    ("publisher/media", "News / media / official page"),
    ("big business", "Big business (over 50K)"),
    ("non-human", "Brand / non-human account"),
    ("account type is", "Business account (old rule)"),
    ("private", "Private account"),
    ("follower count unavailable", "Follower count unavailable"),
    ("INSUFFICIENT_CONTENT", "Too few posts captured"),
    ("LOW_COMPLETENESS", "Incomplete profile data"),
    ("MISSING_CORE_PROFILE", "Missing core profile fields"),
    ("AI_", "AI-dominant content"),
    ("timed out", "Timed out"),
    ("gave up after", "Gave up after retries"),
    ("lease expired", "Runner crashed mid-task"),
    ("429", "Instagram throttled (429)"),
)


def _reason_bucket(reason: Optional[str]) -> str:
    """Collapse task errors into readable groups ('Outside 10K–100K followers', 'Timed out', ...)."""
    if not reason:
        return "Unknown"
    first = re.sub(r"^(ELIGIBILITY|QUALITY_GATE):", "", reason.split(";")[0].strip())
    for needle, label in REASON_LABELS:
        if needle.lower() in first.lower():
            return label
    first = re.sub(r"https?://\S+", "URL", first)
    return re.sub(r"\b\d{3,}\b", "N", first)[:90]


class OpsQueries:
    """Mixin over CreatorIndex (needs connect, creators, task_version, data_version, database, health_file)."""

    def focus(self) -> Optional[Dict[str, Any]]:
        """What the sourcer is exploring right now (niche · city · size)."""
        connection = self.connect()
        try:
            row = connection.execute("SELECT value FROM runtime_flags WHERE name='focus'").fetchone()
        except sqlite3.OperationalError:
            row = None
        finally:
            connection.close()
        return _json(row["value"], None) if row else None

    def stats(self, hours: int = 24) -> Dict[str, Any]:
        """Speed, Instagram request volume, memory/CPU and AI cost for the System page."""
        now = time.time()
        start = (int(now // 3600) - hours + 1) * 3600
        connection = self.connect()
        try:
            def rows(sql: str, *args: Any) -> List[sqlite3.Row]:
                try:
                    return connection.execute(sql, args).fetchall()
                except sqlite3.OperationalError:
                    return []

            buckets = {start + i * 3600: {"ts": start + i * 3600, "kept": 0, "dropped": 0, "page_loads": 0, "api_calls": 0,
                                          "kept_seconds": [], "dropped_seconds": []} for i in range(hours)}
            posts_by_capture = {r["capture_id"]: r["observed_content_count"] for r in rows(
                "SELECT capture_id, observed_content_count FROM profile_captures WHERE captured_at >= ?",
                datetime.fromtimestamp(start, timezone.utc).isoformat())}
            for r in rows("""SELECT kind, state, finished_at, duration, result FROM tasks
                             WHERE finished_at >= ? AND state IN ('done','skipped')""", start):
                bucket = buckets.get(int(r["finished_at"] // 3600) * 3600)
                if bucket is None:
                    continue
                result = _json(r["result"], {}) or {}
                if r["kind"] == "scrape.profile":
                    kept = r["state"] == "done" and result.get("eligible")
                    bucket["kept" if kept else "dropped"] += 1
                    bucket["page_loads"] += 1 + int(posts_by_capture.get(result.get("capture_id"), 0) or 0)
                    if r["duration"]:
                        bucket["kept_seconds" if kept else "dropped_seconds"].append(r["duration"])
                elif r["kind"] == "source.search":
                    bucket["api_calls"] += 1
                elif r["kind"] == "source.similar":
                    bucket["api_calls"] += 2  # user-id lookup + similar accounts
            hourly_status = defaultdict(dict)
            for r in rows("SELECT day, name, count FROM usage WHERE length(day) = 13 AND name IN ('ig_ok','ig_429')"):
                try:
                    hour_ts = time.mktime(time.strptime(r["day"], "%Y-%m-%dT%H"))
                except ValueError:
                    continue
                # Counters are keyed by local hour (IST is UTC+5:30); fold into the chart's hour buckets.
                key = int(hour_ts // 3600) * 3600
                hourly_status[key][r["name"]] = hourly_status[key].get(r["name"], 0) + r["count"]
            series = []
            for bucket in buckets.values():
                status = hourly_status.get(int(bucket["ts"]), {})
                bucket["ig_ok"] = int(status.get("ig_ok", 0))
                bucket["ig_429"] = int(status.get("ig_429", 0))
                kept_s, dropped_s = bucket.pop("kept_seconds"), bucket.pop("dropped_seconds")
                bucket["kept_median_s"] = round(sorted(kept_s)[len(kept_s) // 2], 1) if kept_s else None
                bucket["dropped_median_s"] = round(sorted(dropped_s)[len(dropped_s) // 2], 1) if dropped_s else None
                series.append(bucket)
            metric_series: Dict[str, List[Dict[str, float]]] = defaultdict(list)
            for r in rows("""SELECT CAST(ts / 900 AS INTEGER) * 900 AS t, name, AVG(value) AS v FROM metrics
                             WHERE ts >= ? GROUP BY t, name ORDER BY t""", start):
                metric_series[r["name"]].append({"ts": r["t"], "value": round(r["v"], 1)})
            latest = {r["name"]: r["value"] for r in rows(
                "SELECT name, value FROM metrics WHERE ts = (SELECT MAX(ts) FROM metrics)")}
            usage = {r["name"]: r["total"] for r in rows("SELECT name, SUM(count) AS total FROM usage WHERE length(day) = 10 GROUP BY name")}
            pace_row = rows("SELECT value FROM runtime_flags WHERE name='pace'")
            pace = _json(pace_row[0]["value"], {}) if pace_row else {}
            usage_today = {r["name"]: r["count"] for r in rows("SELECT name, count FROM usage WHERE day = ?", datetime.now().date().isoformat())}
        finally:
            connection.close()
        last = series[-1] if series else {}
        # Speed over the rolling last 60 minutes (not the clock hour, which may have just started).
        connection = self.connect()
        try:
            def rolling(sql: str) -> List[sqlite3.Row]:
                try:
                    return connection.execute(sql, (now - 3600,)).fetchall()
                except sqlite3.OperationalError:
                    return []

            recent = rolling("""SELECT state, duration, json_extract(result,'$.eligible') AS eligible FROM tasks
                                WHERE kind='scrape.profile' AND state IN ('done','skipped') AND finished_at >= ?""")
            hour_ago = time.strftime("%Y-%m-%dT%H", time.localtime(now - 3600))
            this_hour = time.strftime("%Y-%m-%dT%H", time.localtime(now))
            status_rows = connection.execute(
                "SELECT name, SUM(count) FROM usage WHERE day IN (?, ?) AND name IN ('ig_ok','ig_429') GROUP BY name",
                (hour_ago, this_hour)).fetchall() if recent is not None else []
        finally:
            connection.close()
        kept_s = sorted(r["duration"] for r in recent if r["duration"] and r["state"] == "done" and r["eligible"])
        dropped_s = sorted(r["duration"] for r in recent if r["duration"] and not (r["state"] == "done" and r["eligible"]))
        status_now = dict(status_rows)
        last = {**last, "kept": sum(1 for r in recent if r["state"] == "done" and r["eligible"]),
                "dropped": sum(1 for r in recent if not (r["state"] == "done" and r["eligible"])),
                "kept_median_s": round(kept_s[len(kept_s) // 2], 1) if kept_s else last.get("kept_median_s"),
                "dropped_median_s": round(dropped_s[len(dropped_s) // 2], 1) if dropped_s else last.get("dropped_median_s"),
                "ig_ok": int(status_now.get("ig_ok", 0)), "ig_429": int(status_now.get("ig_429", 0))}
        window_kept = sum(b["kept"] for b in series)
        window_loads = sum(b["page_loads"] for b in series)
        window_calls = sum(b["api_calls"] for b in series)
        price = float(os.environ.get("MICROINDIA_LLM_USD_PER_CALL", "0") or 0)
        return {
            "hours": hours,
            "series": series,
            "metrics": metric_series,
            "latest": latest,
            "speed": {
                "profiles_last_hour": (last.get("kept", 0) + last.get("dropped", 0)),
                "kept_last_hour": last.get("kept", 0),
                "kept_median_s": last.get("kept_median_s"),
                "dropped_median_s": last.get("dropped_median_s"),
            },
            "pace": {
                "delay_s": pace.get("delay"),
                "cooling_down": bool(pace.get("cooldown_until") and pace["cooldown_until"] > now),
                "cooldown_left_s": max(0, round((pace.get("cooldown_until") or 0) - now)),
                "last_429": pace.get("last_429"),
                "throttled_last_hour": last.get("ig_429", 0),
                "requests_last_hour": last.get("ig_ok", 0) + last.get("ig_429", 0),
                "throttle_rate_24h": round(sum(b["ig_429"] for b in series) / max(1, sum(b["ig_ok"] + b["ig_429"] for b in series)), 4),
            },
            "requests": {
                "page_loads": window_loads,
                "api_calls": window_calls,
                "per_kept_creator": round((window_loads + window_calls) / window_kept, 1) if window_kept else None,
            },
            "cost": {
                "llm_calls_total": usage.get("llm_calls", 0),
                "llm_calls_today": usage_today.get("llm_calls", 0),
                "llm_usd_total": round(usage.get("llm_calls", 0) * price, 4),
                "scraping_ai_calls": 0,
                "notes": "Scraping and classification make no AI calls; runs on this Mac's Chrome (no proxies, no paid APIs).",
            },
        }

    def health(self) -> Dict[str, Any]:
        try:
            with open(self.health_file) as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}

    def summary(self) -> Dict[str, Any]:
        now = time.time()
        creators = self.creators()
        eligible = [c for c in creators if c["eligible"]]
        day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        connection = self.connect()
        try:
            def scalar(sql: str, *args: Any) -> int:
                try:
                    return int(connection.execute(sql, args).fetchone()[0] or 0)
                except sqlite3.OperationalError:
                    return 0

            found_total = scalar("SELECT COUNT(*) FROM tasks WHERE kind='scrape.profile'")
            scraped_total = scalar("SELECT COUNT(*) FROM tasks WHERE kind='scrape.profile' AND state IN ('done','skipped')")
            queue = scalar("SELECT COUNT(*) FROM tasks WHERE kind='scrape.profile' AND state='queued'")
            in_flight = scalar("SELECT COUNT(*) FROM tasks WHERE state='leased'")
            scraped_hour = scalar(
                "SELECT COUNT(*) FROM tasks WHERE kind='scrape.profile' AND finished_at > ?", now - 3600)
            found_hour = scalar("SELECT COUNT(*) FROM tasks WHERE kind='scrape.profile' AND created_at > ?", now - 3600)
            failed = scalar("SELECT COUNT(*) FROM tasks WHERE state='failed'")
            try:
                flag = connection.execute("SELECT value, updated_at FROM runtime_flags WHERE name='auth_blocked'").fetchone()
                runners = [dict(row) for row in connection.execute("SELECT * FROM runners ORDER BY runner_id")]
            except sqlite3.OperationalError:
                flag, runners = None, []
            version = self.task_version(connection)
        finally:
            connection.close()
        for runner in runners:
            runner["age_seconds"] = round(now - (runner.get("last_heartbeat") or 0), 1)
            runner["alive"] = runner["status"] not in ("stopped", "crashed") and runner["age_seconds"] < 120
        health = self.health()
        return {
            "now": now,
            "version": list(version),
            "eligible_total": len(eligible),
            "eligible_today": sum(1 for c in eligible if (c.get("first_eligible_ts") or 0) >= day_start),
            "captured_today": sum(1 for c in creators if (c.get("first_seen_ts") or 0) >= day_start),
            "brand_ready": sum(1 for c in eligible if c.get("brand_ready")),
            "band": "500–1M",
            "creators_seen": len(creators),
            "scraped_last_hour": scraped_hour,
            "found_last_hour": found_hour,
            "queue": queue,
            "in_flight": in_flight,
            "failed": failed,
            "funnel": {"found": max(found_total, len(creators)), "scraped": len(creators), "kept": len(eligible),
                       "dropped": len(creators) - len(eligible)},
            "focus": self.focus(),
            "auth_blocked": {"reason": flag["value"], "since": flag["updated_at"]} if flag else None,
            "chrome": health.get("chrome"),
            "health_checked_at": health.get("checked_at"),
            "watchdog_actions": health.get("recent_actions", [])[-5:],
            "runners": runners,
            "server_threads": threading.active_count(),
        }

    def timeseries(self, hours: int = 24) -> List[Dict[str, Any]]:
        now = time.time()
        start = (int(now // 3600) - hours + 1) * 3600
        buckets = {start + i * 3600: {"ts": start + i * 3600, "found": 0, "scraped": 0, "eligible": 0} for i in range(hours)}
        connection = self.connect()
        try:
            try:
                for row in connection.execute(
                    "SELECT created_at, finished_at, state, result FROM tasks WHERE kind='scrape.profile' AND (created_at >= ? OR finished_at >= ?)",
                    (start, start),
                ):
                    created = row["created_at"]
                    if created and created >= start:
                        buckets[int(created // 3600) * 3600]["found"] += 1
                    finished = row["finished_at"]
                    if finished and finished >= start and row["state"] in ("done", "skipped"):
                        bucket = buckets[int(finished // 3600) * 3600]
                        bucket["scraped"] += 1
                        if (_json(row["result"], {}) or {}).get("eligible") and not (_json(row["result"], {}) or {}).get("migrated"):
                            bucket["eligible"] += 1
            except sqlite3.OperationalError:
                pass
        finally:
            connection.close()
        return list(buckets.values())

    def activity(self, after: Optional[float] = None, limit: int = 60) -> List[Dict[str, Any]]:
        connection = self.connect()
        try:
            try:
                rows = connection.execute(
                    """SELECT task_id, kind, key, state, attempts, last_error, result, payload, updated_at
                       FROM tasks WHERE state IN ('done','skipped','failed','auth_blocked','queued')
                         AND updated_at > ?
                         AND ((state = 'queued' AND attempts > 0 AND last_error IS NOT NULL)
                              OR (state != 'queued' AND (finished_at IS NOT NULL OR state IN ('failed','auth_blocked'))))
                       ORDER BY updated_at DESC LIMIT ?""",
                    (after or 0, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        finally:
            connection.close()
        events = []
        for row in rows:
            result = _json(row["result"], {}) or {}
            if result.get("migrated"):
                continue
            payload = _json(row["payload"], {})
            event = {
                "id": row["task_id"],
                "ts": row["updated_at"],
                "kind": row["kind"],
                "key": row["key"],
                "state": row["state"],
                "reason": row["last_error"],
                "found": result.get("found"),
                "followers": result.get("followers"),
                "eligible": bool(result.get("eligible")),
                "found_via": payload.get("found_via"),
            }
            if row["state"] == "queued":
                event["state"] = "retrying"
            events.append(event)
        return events

    def pipeline(self) -> Dict[str, Any]:
        connection = self.connect()
        try:
            try:
                kinds: Dict[str, Dict[str, int]] = defaultdict(dict)
                for row in connection.execute("SELECT kind, state, COUNT(*) AS n FROM tasks GROUP BY kind, state"):
                    kinds[row["kind"]][row["state"]] = row["n"]
                failures = Counter(
                    _reason_bucket(row["last_error"])
                    for row in connection.execute("SELECT last_error FROM tasks WHERE state='failed'")
                )
                skips = Counter(
                    _reason_bucket(row["last_error"])
                    for row in connection.execute(
                        """SELECT last_error FROM tasks WHERE state='skipped' AND kind='scrape.profile'
                           AND json_extract(result, '$.migrated') IS NULL ORDER BY finished_at DESC LIMIT 2000""")
                )
                runners = [dict(row) for row in connection.execute("SELECT * FROM runners ORDER BY runner_id")]
                upcoming = [
                    dict(row) for row in connection.execute(
                        "SELECT kind, key, priority, run_at, attempts FROM tasks WHERE state='queued' ORDER BY priority DESC, run_at ASC LIMIT 12")
                ]
            except sqlite3.OperationalError:
                kinds, failures, skips, runners, upcoming = {}, Counter(), Counter(), [], []
        finally:
            connection.close()
        return {
            "kinds": kinds,
            "failures": failures.most_common(15),
            "skip_reasons": skips.most_common(10),
            "runners": runners,
            "upcoming": upcoming,
        }

    def sources(self) -> Dict[str, Any]:
        connection = self.connect()
        try:
            try:
                rows = connection.execute("SELECT state, payload, result FROM tasks WHERE kind='scrape.profile'").fetchall()
            except sqlite3.OperationalError:
                rows = []
        finally:
            connection.close()
        fresh = lambda: {"found": 0, "scraped": 0, "eligible": 0}  # noqa: E731
        by_type: Dict[str, Dict[str, int]] = defaultdict(fresh)
        by_seed: Dict[str, Dict[str, int]] = defaultdict(fresh)
        for row in rows:
            found = _json(row["payload"], {}).get("found_via")
            result = _json(row["result"], {}) or {}
            if result.get("migrated"):
                continue
            for bucket in (by_type[_source_type(found)], by_seed[found or "seed"]):
                bucket["found"] += 1
                if row["state"] in ("done", "skipped"):
                    bucket["scraped"] += 1
                if result.get("eligible"):
                    bucket["eligible"] += 1

        def rows_of(table: Dict[str, Dict[str, int]], top: Optional[int] = None) -> List[Dict[str, Any]]:
            out = [
                {"source": name, **counts,
                 "eligible_rate": round(counts["eligible"] / counts["scraped"], 3) if counts["scraped"] else None}
                for name, counts in table.items()
            ]
            out.sort(key=lambda r: (r["eligible"], r["found"]), reverse=True)
            return out[:top] if top else out

        return {"types": rows_of(by_type), "seeds": rows_of(by_seed, 15)}

    # -- actions --------------------------------------------------------------------

    def unblock(self) -> Dict[str, Any]:
        tasks = TaskStore(self.database)
        try:
            return {"requeued": tasks.clear_auth_blocked()}
        finally:
            tasks.close()

    def retry(self, body: Dict[str, Any]) -> Dict[str, Any]:
        tasks = TaskStore(self.database)
        try:
            now = time.time()
            if body.get("task_id"):
                cursor = tasks.connection.execute(
                    "UPDATE tasks SET state='queued', attempts=0, run_at=?, updated_at=? WHERE task_id=? AND state IN ('failed','skipped')",
                    (now, now, int(body["task_id"])),
                )
            else:
                bucket = str(body.get("reason") or "")
                ids = [
                    row["task_id"] for row in tasks.connection.execute("SELECT task_id, last_error FROM tasks WHERE state='failed'")
                    if not bucket or _reason_bucket(row["last_error"]) == bucket
                ]
                marks = ",".join("?" for _ in ids) or "NULL"
                cursor = tasks.connection.execute(
                    f"UPDATE tasks SET state='queued', attempts=0, run_at=?, updated_at=? WHERE task_id IN ({marks})",
                    (now, now, *ids),
                )
            tasks.connection.commit()
            return {"requeued": cursor.rowcount}
        finally:
            tasks.close()

    def seed(self, body: Dict[str, Any]) -> Dict[str, Any]:
        from ..handlers.common import username_of

        tasks = TaskStore(self.database)
        try:
            added = {"scrape.profile": 0, "source.similar": 0, "source.search": 0}
            for line in str(body.get("usernames") or "").replace(",", "\n").splitlines():
                name = username_of(line.strip())
                if name:
                    added["scrape.profile"] += tasks.enqueue(
                        "scrape.profile", name, {"username": name, "found_via": None}, priority=10, revisit=True)
                    if body.get("expand", True):
                        added["source.similar"] += tasks.enqueue(
                            "source.similar", name, {"username": name}, priority=8, revisit=True)
            for query in [q.strip() for q in str(body.get("queries") or "").splitlines() if q.strip()]:
                added["source.search"] += tasks.enqueue(
                    "source.search", query, {"query": query}, priority=8, revisit=True)
            return {"queued": added}
        finally:
            tasks.close()


# -- routes -------------------------------------------------------------------------


@route("GET", "/api/summary")
def summary(request: Request) -> Any:
    return request.repo.summary()


@route("GET", "/api/timeseries")
def timeseries(request: Request) -> Any:
    return request.repo.timeseries(int(request.params.get("hours", 24)))


@route("GET", "/api/activity")
def activity(request: Request) -> Any:
    after = float(request.params["after"]) if request.params.get("after") else None
    return request.repo.activity(after, int(request.params.get("limit", 60)))


@route("GET", "/api/pipeline")
def pipeline(request: Request) -> Any:
    return request.repo.pipeline()


@route("GET", "/api/stats")
def stats(request: Request) -> Any:
    return request.repo.stats(int(request.params.get("hours", 24)))


@route("GET", "/api/sources")
def sources(request: Request) -> Any:
    return request.repo.sources()


@route("POST", "/api/actions/unblock")
def unblock(request: Request) -> Any:
    return request.repo.unblock()


@route("POST", "/api/actions/retry")
def retry(request: Request) -> Any:
    return request.repo.retry(request.body)


@route("POST", "/api/actions/seed")
def seed(request: Request) -> Any:
    return request.repo.seed(request.body)


@route("GET", "/api/events")
def events(request: Request) -> Stream:
    """Server-Sent Events: push new activity every 2s, summary when anything changed."""
    repo = request.repo

    def run(write: Callable[[bytes], None]) -> None:
        def emit(name: str, payload: Any) -> None:
            write(f"event: {name}\ndata: {json.dumps(payload, default=str)}\n\n".encode())

        cursor = time.time() - 1
        last_version: Any = None
        last_ping = 0.0
        while True:
            items = repo.activity(after=cursor, limit=100)
            if items:
                cursor = max(item["ts"] for item in items)
                for item in reversed(items):
                    emit("activity", item)
            connection = repo.connect()
            try:
                version = (repo.task_version(connection), repo.data_version(connection))
            finally:
                connection.close()
            if version != last_version:
                last_version = version
                emit("summary", repo.summary())
            if time.time() - last_ping > 15:
                write(b": ping\n\n")
                last_ping = time.time()
            time.sleep(2)

    return Stream(run)
