"""Validated, resumable NDJSON input for public Instagram candidates."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple
from urllib.parse import urlparse

RESERVED_PROFILE_ROUTES = {
    "about", "accounts", "developer", "directory", "direct", "emails", "explore", "feed",
    "for_you", "hashtag", "home", "legal", "live", "locations", "oauth", "p", "press",
    "privacy", "push", "reel", "reels", "session", "settings", "stories", "tags", "terms",
    "tv", "web", "your_activity",
}
HANDLE_RE = re.compile(r"^[A-Za-z0-9._]+$")


@dataclass(frozen=True)
class CandidateRecord:
    platform: str
    canonical_profile_url: str
    username: str
    is_private: bool
    source_name: str
    source_record_key: str
    observed_at: str
    follower_count: Optional[int] = None
    category: Optional[str] = None
    country: Optional[str] = None
    niche: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def candidate_key(self) -> str:
        return f"{self.platform}:{self.username.lower()}"

    def as_dict(self) -> Dict[str, Any]:
        result = {
            "platform": self.platform,
            "canonical_profile_url": self.canonical_profile_url,
            "profile_url": self.canonical_profile_url,
            "username": self.username,
            "is_private": self.is_private,
            "source_name": self.source_name,
            "source_record_key": self.source_record_key,
            "observed_at": self.observed_at,
        }
        for key in ("follower_count", "category", "country", "niche"):
            value = getattr(self, key)
            if value is not None:
                result[key] = value
        result.update(self.metadata)
        return result


def canonical_profile_url(value: Any) -> Tuple[str, str]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("profile_url is required")
    parsed = urlparse(value.strip())
    if parsed.scheme.lower() != "https" or parsed.netloc.lower() not in {"instagram.com", "www.instagram.com"}:
        raise ValueError("profile_url must be an HTTPS Instagram profile URL")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 1:
        raise ValueError("profile_url must identify exactly one profile")
    username = parts[0]
    if username.lower() in RESERVED_PROFILE_ROUTES or not HANDLE_RE.fullmatch(username):
        raise ValueError("profile_url uses a reserved or malformed Instagram route")
    username = username.lower()
    return f"https://www.instagram.com/{username}/", username


def validate_candidate_record(raw: Dict[str, Any]) -> CandidateRecord:
    if not isinstance(raw, dict):
        raise ValueError("record must be an object")
    if raw.get("platform") != "instagram":
        raise ValueError("platform must be instagram")
    if raw.get("is_private") is not False:
        raise ValueError("is_private must be the literal false value")
    profile_url = raw.get("profile_url")
    canonical_url, url_username = canonical_profile_url(profile_url)
    username = str(raw.get("username") or "").strip().lower()
    if not username or not HANDLE_RE.fullmatch(username) or username in RESERVED_PROFILE_ROUTES:
        raise ValueError("username is missing or malformed")
    if username != url_username:
        raise ValueError("username does not match profile_url")
    source_name = str(raw.get("source_name") or "").strip()
    source_record_key = str(raw.get("source_record_key") or "").strip()
    observed_at = str(raw.get("observed_at") or "").strip()
    if not source_name or not source_record_key or not observed_at:
        raise ValueError("source_name, source_record_key, and observed_at are required")
    try:
        observed_datetime = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("observed_at must be an ISO-8601 timestamp") from exc
    if observed_datetime.tzinfo is None:
        raise ValueError("observed_at must include a timezone")
    follower_count = raw.get("follower_count")
    if follower_count is not None:
        if isinstance(follower_count, bool) or not isinstance(follower_count, int) or follower_count < 0:
            raise ValueError("follower_count must be a non-negative integer")
    metadata = {
        key: value for key, value in raw.items()
        if key not in {
            "platform", "profile_url", "canonical_profile_url", "username", "is_private",
            "source_name", "source_record_key", "observed_at", "follower_count",
            "category", "country", "niche",
        }
    }
    return CandidateRecord(
        platform="instagram",
        canonical_profile_url=canonical_url,
        username=username,
        is_private=False,
        source_name=source_name,
        source_record_key=source_record_key,
        observed_at=observed_at,
        follower_count=follower_count,
        category=raw.get("category"),
        country=raw.get("country"),
        niche=raw.get("niche"),
        metadata=metadata,
    )


def read_ndjson_batch(path: str, cursor: Optional[str], batch_size: int) -> Iterator[Tuple[int, int, Optional[CandidateRecord], Optional[str], str]]:
    """Yield (line_start, next_cursor, record, error, raw_line) sequentially."""
    if batch_size < 1:
        return
    start = int(cursor or "0")
    source = Path(path)
    with source.open("rb") as handle:
        handle.seek(start)
        for _ in range(batch_size):
            line_start = handle.tell()
            raw_bytes = handle.readline()
            if not raw_bytes:
                return
            next_cursor = handle.tell()
            raw_line = raw_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
            try:
                parsed = json.loads(raw_line)
                record = validate_candidate_record(parsed)
                yield line_start, next_cursor, record, None, raw_line
            except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
                yield line_start, next_cursor, None, f"{type(exc).__name__}: {exc}", raw_line
