"""Dashboard API + live event stream + static SPA host (127.0.0.1 only).

    GET  /api/summary            KPIs, funnel, health, login state
    GET  /api/timeseries         found / scraped / eligible per hour (24h)
    GET  /api/activity?after=ts  recent finished tasks, newest first
    GET  /api/creators           search, filters, sort, paging (?format=csv)
    GET  /api/creators/<handle>  snapshot, metrics, posts, follower history, provenance
    GET  /api/pipeline           tasks by kind/state, runners, failure + skip reasons
    GET  /api/sources            yield per source type and per seed
    GET  /api/events             Server-Sent Events: activity, summary, health
    POST /api/actions/unblock | retry | seed
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import mimetypes
import os
import re
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from .brand import brand_signals
from .eligibility import india_evidence
from .insights import creator_insights
from .constants import SCRAPE_MAX_FOLLOWERS, SCRAPE_MIN_FOLLOWERS
from .runtime.tasks import TaskStore

ELIGIBLE_STATUSES = ("complete", "partial")
REBUILD_SECONDS = 15.0  # creator index rebuild cadence
FOLLOWER_BAND = (SCRAPE_MIN_FOLLOWERS, SCRAPE_MAX_FOLLOWERS)
_META_TEXT = re.compile(r"^\s*[\d.,]+\s*[KkMm]?\s+Followers,", re.I)


def _clean_text(value: Any) -> Optional[str]:
    """Drop Instagram's meta-description fallback ("12K Followers, 3 Following, ... See Instagram photos")."""
    if not value or _META_TEXT.match(str(value)) or "See Instagram photos and videos" in str(value):
        return None
    return str(value).strip() or None


def _clean_name(value: Any) -> Optional[str]:
    name = str(value or "").strip()
    return None if not name or name.lower() in {"instagram", "login • instagram"} else name


def in_band(followers: Any) -> bool:
    return isinstance(followers, (int, float)) and FOLLOWER_BAND[0] <= followers <= FOLLOWER_BAND[1]
DEFAULT_DIST = Path(__file__).resolve().parents[3] / "dashboard" / "dist"
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


def _ts(value: Any) -> Optional[float]:
    """Accept epoch floats/strings or ISO timestamps; return epoch seconds."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return None


def _source_type(found_via: Optional[str]) -> str:
    if not found_via:
        return "seed"
    return found_via.split(":", 1)[0]


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


INDIC_LANGUAGES = {"hi", "ta", "te", "ml", "bn", "mr", "kn", "gu", "pa"}


def _own_words(profile: Dict[str, Any], posts: List[Dict[str, Any]]) -> str:
    """Creator-authored text only (name, bio, captions, post locations, hashtags), never page chrome."""
    text = " ".join(str(_clean_text(profile.get(k)) or "") for k in ("display_name", "bio_text", "location_text"))
    return text + " " + str(profile.get("handle") or "") + " " + " ".join(
        f"{post.get('caption_text') or ''} {post.get('location_text') or ''} {' '.join(post.get('hashtags') or [])}"
        for post in posts
    )


CITIES = {
    "delhi": "Delhi", "new delhi": "Delhi", "dilli": "Delhi", "noida": "Delhi NCR", "gurgaon": "Delhi NCR", "gurugram": "Delhi NCR",
    "mumbai": "Mumbai", "bombay": "Mumbai", "thane": "Mumbai", "bangalore": "Bengaluru", "bengaluru": "Bengaluru",
    "hyderabad": "Hyderabad", "chennai": "Chennai", "kolkata": "Kolkata", "calcutta": "Kolkata", "pune": "Pune",
    "ahmedabad": "Ahmedabad", "jaipur": "Jaipur", "lucknow": "Lucknow", "chandigarh": "Chandigarh", "indore": "Indore",
    "kochi": "Kochi", "cochin": "Kochi", "trivandrum": "Thiruvananthapuram", "thiruvananthapuram": "Thiruvananthapuram",
    "goa": "Goa", "surat": "Surat", "nagpur": "Nagpur", "bhopal": "Bhopal", "coimbatore": "Coimbatore", "guwahati": "Guwahati",
    "bhubaneswar": "Bhubaneswar", "patna": "Patna", "vizag": "Visakhapatnam", "visakhapatnam": "Visakhapatnam",
    "mysore": "Mysuru", "mysuru": "Mysuru", "dehradun": "Dehradun", "amritsar": "Amritsar", "ludhiana": "Ludhiana",
    "kanpur": "Kanpur", "varanasi": "Varanasi", "madurai": "Madurai", "udaipur": "Udaipur", "jodhpur": "Jodhpur",
    "kerala": "Kerala", "punjab": "Punjab",
}
_CITY_RE = re.compile(r"(?<![a-z])(" + "|".join(sorted(map(re.escape, CITIES), key=len, reverse=True)) + r")(?![a-z])")


def _city(profile: Dict[str, Any], posts: List[Dict[str, Any]]) -> Optional[str]:
    """Most-mentioned Indian city across bio, location and posts (hashtags like #punefood count)."""
    text = _own_words(profile, posts).lower()
    counts: Counter = Counter()
    for match in _CITY_RE.findall(text):
        counts[CITIES[match]] += 1
    for tag in re.findall(r"#([a-z0-9_]+)", text):
        for key, name in CITIES.items():
            if " " not in key and len(key) >= 4 and key in tag:
                counts[name] += 1
                break
    bio = " ".join(str(profile.get(k) or "") for k in ("bio_text", "location_text")).lower()
    for match in _CITY_RE.findall(bio):
        counts[CITIES[match]] += 3  # the creator's own statement weighs more
    return counts.most_common(1)[0][0] if counts else None


def _last_post(posts: List[Dict[str, Any]]) -> Optional[float]:
    stamps = [_ts(p.get("published_at")) for p in posts if p.get("published_at") and not p.get("is_pinned")]
    stamps = [s for s in stamps if s]
    return max(stamps) if stamps else None


def _kind(profile: Dict[str, Any]) -> str:
    """'small business' or 'creator' (big businesses never reach the dataset)."""
    from .eligibility import BUSINESS_TERMS, _words

    label = str(profile.get("account_type") or "").lower()
    text = " ".join(str(profile.get(k) or "") for k in ("handle", "display_name", "bio_text"))
    return "small business" if label == "business" or (_words(text) & BUSINESS_TERMS) else "creator"


def _excluded(profile: Dict[str, Any]) -> str:
    from .eligibility import _plain, ai_or_repost_reason

    reason = ai_or_repost_reason(str(profile.get("handle") or ""), str(profile.get("display_name") or ""),
                                 _plain(str(profile.get("bio_text") or "")))
    if not reason and (profile.get("ai_label") == "ai_dominant" or "ai-generated" in str(profile.get("account_type") or "").lower()):
        reason = "AI-generated persona"
    return reason


def _category(profile: Dict[str, Any], posts: List[Dict[str, Any]]) -> Optional[str]:
    from .intelligence import primary_category_of

    return primary_category_of(_own_words(profile, posts)) or "other"


def _engagement(profile: Dict[str, Any], posts: List[Dict[str, Any]], feature: Dict[str, Any]) -> Optional[float]:
    """Median (likes + comments) / followers over recent posts; robust to one viral reel."""
    followers = profile.get("follower_count") or feature.get("follower_count")
    rates = sorted(
        ((post.get("like_count") or 0) + (post.get("comment_count") or 0)) / followers
        for post in posts if followers and post.get("like_count") is not None and not post.get("is_pinned")
    )[:]
    if not rates:
        return feature.get("engagement_rate")
    middle = len(rates) // 2
    return rates[middle] if len(rates) % 2 else (rates[middle - 1] + rates[middle]) / 2


def _india(profile: Dict[str, Any], posts: List[Dict[str, Any]]) -> List[str]:
    # Stored language_signals on older captures came from the whole page (footer included); ignore them.
    return india_evidence(_own_words(profile, posts))


def _languages(profile: Dict[str, Any], posts: List[Dict[str, Any]]) -> List[str]:
    from .e2e import _language_signals

    return _language_signals(_own_words(profile, posts))


class Repository:
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

    # -- live operations ------------------------------------------------------------

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
        from .handlers.common import username_of

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


class Handler(BaseHTTPRequestHandler):
    repo: Repository
    dist: Path
    server_version = "microindia-api/1"

    def log_message(self, format: str, *args: Any) -> None:  # quiet access log
        return

    def handle(self) -> None:
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError, TimeoutError):
            pass  # browser closed the stream (tab closed, reload); nothing to report

    def _send(self, status: int, body: bytes, content_type: str, extra: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if content_type.startswith("application/json") else "public, max-age=60")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(status, json.dumps(payload, default=str).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        started = time.time()
        try:
            self._get()
        finally:
            elapsed = time.time() - started
            if elapsed > 2 and not self.path.startswith("/api/events"):
                print(json.dumps({"event": "slow_request", "path": self.path[:120], "seconds": round(elapsed, 2),
                                  "threads": threading.active_count()}), flush=True)

    def _get(self) -> None:
        parsed = urlparse(self.path)
        params = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        path = parsed.path
        try:
            if path == "/api/summary":
                return self._json(self.repo.summary())
            if path == "/api/timeseries":
                return self._json(self.repo.timeseries(int(params.get("hours", 24))))
            if path == "/api/activity":
                after = float(params["after"]) if params.get("after") else None
                return self._json(self.repo.activity(after, int(params.get("limit", 60))))
            if path == "/api/creators":
                result = self.repo.list_creators(params)
                if "csv" in result:
                    return self._send(200, result["csv"].encode(), "text/csv; charset=utf-8",
                                      {"Content-Disposition": "attachment; filename=microindia-creators.csv"})
                return self._json(result)
            if path.startswith("/api/creators/"):
                detail = self.repo.creator_detail(unquote(path.rsplit("/", 1)[-1]))
                return self._json(detail) if detail else self._json({"error": "not found"}, 404)
            if path == "/api/pipeline":
                return self._json(self.repo.pipeline())
            if path == "/api/stats":
                return self._json(self.repo.stats(int(params.get("hours", 24))))
            if path == "/api/sources":
                return self._json(self.repo.sources())
            if path == "/api/events":
                return self._events()
            if path.startswith("/api/"):
                return self._json({"error": "unknown endpoint"}, 404)
            return self._static(path)
        except BrokenPipeError:
            return
        except Exception as exc:  # surface errors to the UI instead of hanging
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = _json(self.rfile.read(length).decode() if length else "", {}) or {}
        actions = {
            "/api/actions/unblock": lambda: self.repo.unblock(),
            "/api/actions/retry": lambda: self.repo.retry(body),
            "/api/actions/seed": lambda: self.repo.seed(body),
            "/api/search/chat": lambda: self._chat(body),
            "/api/assistant": lambda: self._assistant(body),
        }
        action = actions.get(urlparse(self.path).path)
        if action is None:
            return self._json({"error": "unknown action"}, 404)
        try:
            return self._json(action())
        except Exception as exc:
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def _assistant(self, body: Dict[str, Any]) -> Dict[str, Any]:
        from .assistant import answer

        return answer(self.repo, str(body.get("message") or ""), body.get("history") or [], body.get("filters") or {})

    def _chat(self, body: Dict[str, Any]) -> Dict[str, Any]:
        from .search_ai import chat_search

        answer = chat_search(str(body.get("message") or ""), body.get("criteria") or {})
        params = {**answer["criteria"], "sort": "engagement", "limit": "5"}
        preview = self.repo.list_creators(params)
        answer["matches"] = preview["total"]
        return answer

    def _events(self) -> None:
        """Server-Sent Events: push new activity every 2s, summary when anything changed."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        cursor = time.time() - 1
        last_version: Any = None
        last_ping = 0.0
        while True:
            events = self.repo.activity(after=cursor, limit=100)
            if events:
                cursor = max(event["ts"] for event in events)
                for event in reversed(events):
                    self._emit("activity", event)
            connection = self.repo.connect()
            try:
                version = (self.repo.task_version(connection), self.repo.data_version(connection))
            finally:
                connection.close()
            if version != last_version:
                last_version = version
                self._emit("summary", self.repo.summary())
            if time.time() - last_ping > 15:
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()
                last_ping = time.time()
            time.sleep(2)

    def _emit(self, name: str, payload: Any) -> None:
        self.wfile.write(f"event: {name}\ndata: {json.dumps(payload, default=str)}\n\n".encode())
        self.wfile.flush()

    def _static(self, path: str) -> None:
        target = (self.dist / path.lstrip("/")).resolve()
        if not str(target).startswith(str(self.dist.resolve())) or not target.is_file():
            target = self.dist / "index.html"
        if not target.is_file():
            body = b"<h1>Dashboard not built</h1><p>Run <code>npm run build</code> in apps/dashboard.</p>"
            return self._send(200, body, "text/html; charset=utf-8")
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        extra = {"Cache-Control": "no-cache"} if target.name == "index.html" else None
        self._send(200, target.read_bytes(), content_type, extra)


def serve(database: str, host: str, port: int, dist: Path, health_file: str) -> None:
    Handler.repo = Repository(database, health_file)
    Handler.dist = dist
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    print(json.dumps({"event": "api_listening", "url": f"http://{host}:{port}", "dist": str(dist)}), flush=True)
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="microIndia dashboard API")
    parser.add_argument("--database", default=os.environ.get("MICROINDIA_DATABASE", "data/microindia.sqlite3"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--dist", default=str(DEFAULT_DIST))
    parser.add_argument("--health-file", default="run/health.json")
    args = parser.parse_args()
    serve(args.database, args.host, args.port, Path(args.dist), args.health_file)


if __name__ == "__main__":
    main()
