"""Small helpers shared by sourcer and scraper handlers."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from ..cohort import canonical_profile_url
from ..runtime.results import FollowUp

MENTION_RE = re.compile(r"@([A-Za-z0-9._]{1,30})")


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
