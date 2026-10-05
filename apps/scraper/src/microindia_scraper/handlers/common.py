"""Small helpers shared by sourcer and scraper handlers."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from ..runtime.results import FollowUp

MENTION_RE = re.compile(r"@([A-Za-z0-9._]{1,30})")
# Instagram paths that look like usernames but are app routes.
RESERVED_PROFILE_ROUTES = {
    "about", "accounts", "developer", "direct", "directory", "emails", "explore", "feed",
    "for_you", "hashtag", "home", "legal", "live", "locations", "oauth", "p", "press",
    "privacy", "push", "reel", "reels", "stories", "tags", "terms", "tv", "web",
}


def _profile_url(username: str) -> str:
    handle = re.sub(r"[^A-Za-z0-9._]", "", str(username)).strip(".").lower()
    if not handle or handle in RESERVED_PROFILE_ROUTES:
        return ""
    return f"https://www.instagram.com/{handle}/"


def canonical_profile_url(value: Any) -> str:
    """Return one canonical public profile URL for a username or profile URL, or an empty string."""
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if not candidate:
        return ""
    if "://" not in candidate:
        return _profile_url(candidate)
    parsed = urlparse(candidate)
    if parsed.scheme.lower() != "https" or parsed.netloc.lower() not in {"instagram.com", "www.instagram.com"}:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 1 or parts[0].lower() in RESERVED_PROFILE_ROUTES:
        return ""
    return _profile_url(parts[0])


def username_of(value: Any) -> Optional[str]:
    """Normalise a username, @handle or profile URL to a lowercase username."""
    if not isinstance(value, str):
        return None
    url = canonical_profile_url(value.strip().lstrip("@"))
    return url.rstrip("/").rsplit("/", 1)[-1] if url else None


def profile_scrape(username: str, *, priority: int = 0, **payload: Any) -> FollowUp:
    return FollowUp(
        kind="scrape.profile",
        key=username,
        payload={"username": username, **{k: v for k, v in payload.items() if v is not None}},
        priority=priority,
    )


def mentioned_usernames(texts: Iterable[Any], *, exclude: Iterable[str] = ()) -> List[str]:
    skip = {name.lower() for name in exclude}
    found: Dict[str, None] = {}
    for text in texts:
        values = text if isinstance(text, list) else MENTION_RE.findall(str(text or ""))
        for value in values:
            name = username_of(str(value))
            if name and name not in skip:
                found[name] = None
    return list(found)
