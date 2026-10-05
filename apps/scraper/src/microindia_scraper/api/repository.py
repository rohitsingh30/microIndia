"""Creator index: the read model over the capture tables that every creator screen uses.

CreatorIndex builds one dict per scraped profile (rebuilt in the background when captures change) and
serves list_creators, creator_detail, similar creators and provenance. OpsQueries (ops.py) adds the
live-operations queries; server.Repository combines both.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from ..brand import brand_signals
from ..constants import SCRAPE_MAX_FOLLOWERS, SCRAPE_MIN_FOLLOWERS
from ..insights import creator_insights
from ..profile_text import (
    _category, _city, _clean_name, _clean_text, _engagement, _excluded, _india, _kind, _languages, _last_post,
    _own_words, _ts,
)

ELIGIBLE_STATUSES = ("complete", "partial")
REBUILD_SECONDS = 15.0  # creator index rebuild cadence
FOLLOWER_BAND = (SCRAPE_MIN_FOLLOWERS, SCRAPE_MAX_FOLLOWERS)


def in_band(followers: Any) -> bool:
    return isinstance(followers, (int, float)) and FOLLOWER_BAND[0] <= followers <= FOLLOWER_BAND[1]


SORTS = {
    "followers": lambda c: c.get("followers") or 0,
    "engagement": lambda c: c.get("engagement_rate") or 0,
    "recent": lambda c: c.get("captured_ts") or 0,
    "likes": lambda c: c.get("median_likes") or 0,
    "handle": lambda c: c.get("handle") or "",
    "brand": lambda c: (c.get("brand_posts") or 0, c.get("engagement_rate") or 0),
}


def _json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _source_type(found_via: Optional[str]) -> str:
    if not found_via:
        return "seed"
    return found_via.split(":", 1)[0]


class CreatorIndex:
    """Read model over the capture tables and the task table. Creators are cached per data version."""

    def __init__(self, database: str, health_file: str = "run/health.json") -> None:
        self.database = database
        self.health_file = health_file
        self._lock = threading.Lock()
        self._creators: List[Dict[str, Any]] = []
        self._creators_version: Optional[Tuple[Any, ...]] = None
        self._built_at = 0.0
        self._derived: Dict[Tuple[str, int], Dict[str, Any]] = {}  # per-capture derived fields
        self._rebuilding = threading.Lock()  # one rebuild at a time; others get the last good copy

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    # -- versioning ---------------------------------------------------------------

    def data_version(self, connection: sqlite3.Connection) -> Tuple[Any, ...]:
        captures = connection.execute("SELECT COUNT(*), MAX(captured_at) FROM profile_captures").fetchone()
        features = connection.execute("SELECT MAX(calculated_at) FROM creator_features").fetchone()
        return (captures[0], captures[1], features[0])

    def task_version(self, connection: sqlite3.Connection) -> Tuple[Any, ...]:
        try:
            row = connection.execute("SELECT COUNT(*), MAX(updated_at) FROM tasks").fetchone()
            return (row[0], row[1])
        except sqlite3.OperationalError:
            return (0, None)

    # -- creators -------------------------------------------------------------------

    def creators(self) -> List[Dict[str, Any]]:
        """Creator index. The first call builds it; afterwards changes are rebuilt in the background
        (at most every REBUILD_SECONDS) and requests always get the last complete copy instantly."""
        with self._lock:
            have_copy = self._creators_version is not None
            stale = time.time() - self._built_at >= REBUILD_SECONDS
        if not have_copy:
            with self._rebuilding:
                with self._lock:
                    if self._creators_version is not None:
                        return self._creators
                self._rebuild()
        elif stale and self._rebuilding.acquire(blocking=False):
            def run() -> None:
                try:
                    self._rebuild()
                finally:
                    self._rebuilding.release()
            threading.Thread(target=run, daemon=True).start()
        with self._lock:
            return self._creators

    def _rebuild(self) -> None:
        started = time.time()
        connection = self.connect()
        try:
            version = self.data_version(connection)
            with self._lock:
                if version == self._creators_version:
                    self._built_at = time.time()
                    return
            built = self._build_creators(connection)
            with self._lock:
                self._creators, self._creators_version, self._built_at = built, version, time.time()
            print(json.dumps({"event": "index_rebuilt", "creators": len(built), "seconds": round(time.time() - started, 2)}), flush=True)
        except Exception as exc:  # never leave the index silently stale
            print(json.dumps({"event": "index_rebuild_failed", "error": repr(exc), "seconds": round(time.time() - started, 2)}), flush=True)
            with self._lock:
                self._built_at = time.time() - REBUILD_SECONDS + 5  # retry soon
            raise
        finally:
            connection.close()

    def _build_creators(self, connection: sqlite3.Connection) -> List[Dict[str, Any]]:
        captures: Dict[str, List[sqlite3.Row]] = defaultdict(list)
        for row in connection.execute(
            """SELECT c.capture_id, c.profile_url, c.status, c.captured_at, c.completeness_score,
                      c.observed_content_count, p.payload AS profile
               FROM profile_captures c LEFT JOIN profile_snapshots p ON p.capture_id = c.capture_id
               ORDER BY c.captured_at"""
        ):
            captures[row["profile_url"]].append(row)
        features = {
            row["profile_url"]: _json(row["payload"], {})
            for row in connection.execute(
                """SELECT c.profile_url, f.payload FROM creators c
                   JOIN creator_features f ON f.creator_id = c.creator_id"""
            )
        }
        found_via = self._found_via(connection)
        posts_by_capture: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in connection.execute(
            """SELECT s.capture_id, s.payload FROM content_snapshots s
               JOIN (SELECT profile_url, MAX(captured_at) AS latest FROM profile_captures GROUP BY profile_url) l
               JOIN profile_captures c ON c.profile_url = l.profile_url AND c.captured_at = l.latest
               WHERE s.capture_id = c.capture_id"""
        ):
            posts_by_capture[row["capture_id"]].append(_json(row["payload"], {}))
        creators = []
        for url, rows in captures.items():
            latest = rows[-1]
            with_profile = [row for row in rows if row["profile"]]
            profile = _json(with_profile[-1]["profile"], {}) if with_profile else {}
            feature = features.get(url, {})
            posts = posts_by_capture.get(latest["capture_id"], [])
            derived = self._derive(latest["capture_id"], profile, posts)
            handle = (profile.get("handle") or url.rstrip("/").rsplit("/", 1)[-1]).lower()
            eligible_rows = [row for row in rows if row["status"] in ELIGIBLE_STATUSES]
            history = [
                {"ts": _ts(row["captured_at"]), "followers": _json(row["profile"], {}).get("follower_count")}
                for row in with_profile
            ]
            creators.append({
                "handle": handle,
                "url": url,
                "name": _clean_name(profile.get("display_name")),
                "bio": _clean_text(profile.get("bio_text")),
                "location": _clean_text(profile.get("location_text")),
                "external_url": profile.get("external_url"),
                "verified": bool(profile.get("is_verified")),
                "followers": profile.get("follower_count") or feature.get("follower_count"),
                "following": profile.get("following_count"),
                "posts": profile.get("post_count"),
                "languages": derived["languages"],
                "category": derived["category"],
                "kind": _kind(profile),
                "engagement_rate": _engagement(profile, posts_by_capture.get(latest["capture_id"], []), feature),
                "median_likes": feature.get("median_likes"),
                "median_comments": feature.get("median_comments"),
                "median_views": feature.get("median_views"),
                "top_hashtags": [
                    str(tag.get("tag") if isinstance(tag, dict) else tag) for tag in (feature.get("top_hashtags") or [])[:8]
                ],
                "content_mix": feature.get("content_type_mix") or {},
                "commercial_rate": feature.get("commercial_disclosure_rate"),
                "data_quality": feature.get("data_quality"),
                "ai_label": profile.get("ai_label"),
                "status": latest["status"],
                # Older captures predate some gates (band, AI/repost); enforce them here too.
                "eligible": bool(eligible_rows) and latest["status"] in ELIGIBLE_STATUSES
                and in_band(profile.get("follower_count") or feature.get("follower_count"))
                and not _excluded(profile),
                "excluded_reason": _excluded(profile),
                "completeness": latest["completeness_score"],
                "captured_at": latest["captured_at"],
                "captured_ts": _ts(latest["captured_at"]),
                "first_seen_ts": _ts(rows[0]["captured_at"]),
                "first_eligible_ts": _ts(eligible_rows[0]["captured_at"]) if eligible_rows else None,
                "captures": len(rows),
                "capture_id": latest["capture_id"],
                "found_via": found_via.get(handle),
                **derived["brand"],
                "india_signals": derived["india"],
                "city": derived["city"],
                "search_text": derived["search_text"],
                "last_post_ts": derived["last_post_ts"],
                "follower_history": [point for point in history if point["followers"] is not None],
            })
        return creators

    def _derive(self, capture_id: str, profile: Dict[str, Any], posts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Text-derived fields for one capture; cached because a capture never changes once its posts are in."""
        key = (capture_id, len(posts))
        cached = self._derived.get(key)
        if cached is None:
            cached = {
                "category": _category(profile, posts),
                "languages": _languages(profile, posts),
                "brand": brand_signals(profile, posts),
                "india": _india(profile, posts),
                "city": _city(profile, posts),
                "search_text": _own_words(profile, posts)[:4000].lower(),
                "last_post_ts": _last_post(posts),
            }
            self._derived[key] = cached
        return cached

    def _found_via(self, connection: sqlite3.Connection) -> Dict[str, Optional[str]]:
        try:
            return {
                row["key"]: json.loads(row["payload"]).get("found_via")
                for row in connection.execute("SELECT key, payload FROM tasks WHERE kind='scrape.profile'")
            }
        except sqlite3.OperationalError:
            return {}

    def list_creators(self, params: Dict[str, str]) -> Dict[str, Any]:
        items = self.creators()
        # Everything we scraped is listed; "eligible" narrows to profiles inside the guardrails.
        scope = params.get("scope", "all")
        if scope == "eligible":
            items = [c for c in items if c["eligible"]]

        query = params.get("q", "").strip().lower()
        if query:
            items = [
                c for c in items
                if query in " ".join(str(c.get(k) or "") for k in ("handle", "name", "bio", "location", "category")).lower()
                or any(query in tag.lower() for tag in c["top_hashtags"])
            ]

        def number(key: str) -> Optional[float]:
            try:
                return float(params[key]) if params.get(key) not in (None, "") else None
            except ValueError:
                return None

        for key, field, cmp in (
            ("min_followers", "followers", lambda v, t: v >= t),
            ("max_followers", "followers", lambda v, t: v <= t),
            ("min_engagement", "engagement_rate", lambda v, t: v >= t),
        ):
            threshold = number(key)
            if threshold is not None:
                items = [c for c in items if c.get(field) is not None and cmp(c[field], threshold)]
        if params.get("category"):
            items = [c for c in items if (c.get("category") or "") == params["category"]]
        if params.get("language"):
            items = [c for c in items if params["language"] in (c.get("languages") or [])]
        if params.get("city"):
            items = [c for c in items if c.get("city") == params["city"]]
        if params.get("active_days"):
            cutoff = time.time() - float(params["active_days"]) * 86400
            items = [c for c in items if (c.get("last_post_ts") or 0) >= cutoff]
        if params.get("kind"):
            items = [c for c in items if c.get("kind") == params["kind"]]
        if params.get("india") == "1":
            items = [c for c in items if c.get("india_signals")]
        if params.get("brand") == "1":
            items = [c for c in items if c.get("brand_ready")]
        if params.get("source"):
            items = [c for c in items if _source_type(c.get("found_via")) == params["source"]]
        fresh = number("fresh_days")
        if fresh is not None:
            cutoff = time.time() - fresh * 86400
            items = [c for c in items if (c.get("captured_ts") or 0) >= cutoff]
        sort = params.get("sort", "recent")
        items = sorted(items, key=SORTS.get(sort, SORTS["recent"]), reverse=params.get("order", "desc") != "asc")
        total = len(items)
        facets = {
            "category": Counter(c.get("category") or "other" for c in items).most_common(12),
            "language": Counter(lang for c in items for lang in c.get("languages") or []).most_common(8),
            "source": Counter(_source_type(c.get("found_via")) for c in items).most_common(),
            "brand_ready": sum(1 for c in items if c.get("brand_ready")),
            "india": sum(1 for c in items if c.get("india_signals")),
            "kind": Counter(c.get("kind") or "creator" for c in items).most_common(),
            "city": Counter(c["city"] for c in items if c.get("city")).most_common(14),
        }
        if params.get("format") == "csv":
            return {"csv": self._csv(items)}
        limit = min(500, int(params.get("limit") or 60))
        offset = int(params.get("offset") or 0)
        page = [{k: v for k, v in c.items() if k not in ("follower_history", "search_text")} for c in items[offset:offset + limit]]
        return {"total": total, "items": page, "facets": facets}

    @staticmethod
    def _csv(items: List[Dict[str, Any]]) -> str:
        columns = ["handle", "name", "followers", "engagement_rate", "median_likes", "median_comments", "brand_posts",
                   "paid_partnerships", "open_to_collabs", "category", "city",
                   "languages", "location", "bio", "url", "external_url", "captured_at", "found_via"]
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(columns)
        for c in items:
            writer.writerow([";".join(c[k]) if isinstance(c.get(k), list) else c.get(k) for k in columns])
        return buffer.getvalue()

    def strip(self, creator: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in creator.items() if k != "search_text"}

    def creator_detail(self, handle: str) -> Optional[Dict[str, Any]]:
        handle = handle.lower().lstrip("@")
        creator = next((c for c in self.creators() if c["handle"] == handle), None)
        if creator is None:
            return None
        connection = self.connect()
        try:
            content = [
                _json(row["payload"], {})
                for row in connection.execute(
                    "SELECT payload FROM content_snapshots WHERE capture_id=? ORDER BY content_index",
                    (creator["capture_id"],),
                )
            ]
            metrics_row = connection.execute(
                "SELECT payload FROM metric_snapshots WHERE capture_id=? ORDER BY metric_id DESC LIMIT 1",
                (creator["capture_id"],),
            ).fetchone()
            capture = connection.execute(
                "SELECT warnings, missing_fields FROM profile_captures WHERE capture_id=?", (creator["capture_id"],)
            ).fetchone()
            task = self._task_row(connection, "scrape.profile", handle)
        finally:
            connection.close()
        posts = [
            {
                "permalink": item.get("permalink"),
                "type": item.get("content_type"),
                "caption": item.get("caption_text"),
                "published_at": item.get("published_at"),
                "likes": item.get("like_count"),
                "comments": item.get("comment_count"),
                "views": item.get("view_count"),
                "hashtags": item.get("hashtags") or [],
                "mentions": item.get("mentions") or [],
                "location": item.get("location_text"),
                "paid_partnership": bool(item.get("is_paid_partnership")),
            }
            for item in content
        ]
        return {
            **self.strip(creator),
            "metrics": _json(metrics_row["payload"], {}) if metrics_row else {},
            "posts_sample": posts,
            "warnings": _json(capture["warnings"], []) if capture else [],
            "missing_fields": _json(capture["missing_fields"], []) if capture else [],
            "reasons": (_json(task["result"], {}) or {}).get("reasons", []) if task else [],
            "provenance": self.provenance(handle),
            "insights": creator_insights(creator, content, self.creators()),
            "similar": self._similar_to(handle),
        }

    def _similar_to(self, handle: str, limit: int = 12) -> List[Dict[str, Any]]:
        """Creators Instagram suggested as similar to this one that we have already scraped."""
        by_handle = {c["handle"]: c for c in self.creators()}
        connection = self.connect()
        try:
            try:
                keys = [row["key"] for row in connection.execute(
                    "SELECT key FROM tasks WHERE kind='scrape.profile' AND json_extract(payload, '$.found_via') = ?",
                    (f"similar:{handle}",))]
            except sqlite3.OperationalError:
                keys = []
        finally:
            connection.close()
        found = [by_handle[key] for key in keys if key in by_handle]
        found.sort(key=lambda c: (c.get("engagement_rate") or 0), reverse=True)
        return [{k: c.get(k) for k in ("handle", "name", "followers", "engagement_rate", "category", "eligible")} for c in found[:limit]]

    @staticmethod
    def _task_row(connection: sqlite3.Connection, kind: str, key: str) -> Optional[sqlite3.Row]:
        try:
            return connection.execute("SELECT * FROM tasks WHERE kind=? AND key=?", (kind, key)).fetchone()
        except sqlite3.OperationalError:
            return None

    def provenance(self, handle: str, depth: int = 6) -> List[Dict[str, str]]:
        """How a creator was found: [{via: 'similar', from: 'seedhandle'}, ...] back to a seed/query."""
        chain: List[Dict[str, str]] = []
        connection = self.connect()
        try:
            current, seen = handle, set()
            while current and current not in seen and len(chain) < depth:
                seen.add(current)
                row = self._task_row(connection, "scrape.profile", current)
                found = _json(row["payload"], {}).get("found_via") if row else None
                if not found:
                    chain.append({"via": "seed" if row else "unknown", "from": ""})
                    break
                kind, _, origin = found.partition(":")
                chain.append({"via": kind, "from": origin})
                current = origin if kind in ("similar", "mention") else None
        finally:
            connection.close()
        return chain
