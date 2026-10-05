"""Text derived from a creator's own words: clean fields, city, kind, category, languages, India evidence.

Shared by the API's creator index and the scraping handlers. Everything here reads only what the creator
wrote (name, bio, location, captions, post locations, hashtags), never Instagram's page chrome.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .niches import CITY_ALIASES

_META_TEXT = re.compile(r"^\s*[\d.,]+\s*[KkMm]?\s+Followers,", re.I)
# Instagram's generic meta description: "63K Followers, 1,536 Following, 1,810 Posts - See Instagram photos and
# videos from Name (@handle)". It is page chrome, never the account's bio or location.
GENERIC_DESCRIPTION = re.compile(
    r"[\d.,]+\s*[KkMm]?\s+Followers?,\s*[\d.,]+\s*[KkMm]?\s+Following,\s*[\d.,]+\s*[KkMm]?\s+Posts?"
    r"(?:\s*[-–]\s*See Instagram photos and videos from\s+[^\n]*?\(@[\w.]+\)(?:\s+[\w.]+(?=\s*$))?)?",
    re.I,
)
_DESCRIPTION_NAME = re.compile(r"(?:See Instagram photos and videos from|Posts\s*[-–])\s+(.+?)\s+\(@([\w.]+)\)", re.I)
_DESCRIPTION_BIO = re.compile(r"\(@[\w.]+\)\s+on Instagram:\s*[\"“](.+?)[\"”]\s*$", re.I | re.S)
# Lines of Instagram's own UI that end up in header text: badges, buttons, the highlights tray.
UI_LINES = re.compile(
    r"^(ai[- ]generated profile|ai info|highlights|threads|more|see translation|follow|following|message|contact|"
    r"email|call|directions|book now|order food|view shop|edit profile|share profile|options|follow back|"
    r"subscribe|verified)$",
    re.I,
)
_LABEL_LINE = re.compile(r"^(?P<label>[^\n•|📍@#]{2,48}?)\s*•\s*$")
PLACEHOLDER_NAMES = {"instagram", "login • instagram", "log in • instagram", "instagram photos and videos"}


def is_generic_description(value: Any) -> bool:
    """True when a text is (or is mostly) Instagram's "N Followers, N Following, N Posts - See Instagram…" chrome."""
    text = str(value or "").strip()
    if not text:
        return False
    match = GENERIC_DESCRIPTION.search(text)
    if not match:
        return "See Instagram photos and videos" in text
    rest = (text[:match.start()] + text[match.end():]).strip()
    return not rest or "See Instagram photos and videos" in text and len(rest) < 3


def strip_generic_description(value: Any) -> Optional[str]:
    """Remove Instagram's generic description from a text; None when nothing of the account's own is left."""
    text = GENERIC_DESCRIPTION.sub(" ", str(value or ""))
    text = re.sub(r"See Instagram photos and videos from[^\n]*", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    return text or None


def name_from_description(value: Any, handle: str = "") -> Optional[str]:
    """The display name inside Instagram's meta description ("… from Asha Rao (@asha)")."""
    match = _DESCRIPTION_NAME.search(str(value or ""))
    if not match or (handle and match.group(2).lower() != handle.lower()):
        return None
    return clean_display_name(match.group(1))


def bio_from_description(value: Any) -> Optional[str]:
    """The quoted bio in the other description form ("… - Asha (@asha) on Instagram: \"bio\"")."""
    match = _DESCRIPTION_BIO.search(str(value or ""))
    return match.group(1).strip() if match else None


def clean_display_name(value: Any) -> Optional[str]:
    """A real display name, or None for Instagram's placeholder title ("Instagram")."""
    name = str(value or "").strip()
    return None if not name or name.lower() in PLACEHOLDER_NAMES or is_generic_description(name) else name


def split_bio(value: Any) -> Dict[str, Any]:
    """Separate an Instagram bio as captured into the account's own words and the UI around them.

    Returns ``bio`` (own lines only), ``label`` (the "Makeup Artist •" category line) and ``ui`` (the
    UI lines dropped, e.g. "AI-generated profile"). The highlights tray ("Highlights", then highlight
    titles) ends the bio.
    """
    label: Optional[str] = None
    ui: List[str] = []
    lines: List[str] = []
    text = strip_generic_description(value) or ""
    for index, raw in enumerate(text.splitlines()):
        line = raw.strip()
        if not line:
            continue
        if line.lower() == "highlights":
            ui.append(line)
            break
        if UI_LINES.match(line):
            ui.append(line)
            continue
        found = _LABEL_LINE.match(line)
        if found and label is None and not lines:
            label = found.group("label").strip()
            continue
        lines.append(line)
    return {"bio": "\n".join(lines) or None, "label": label, "ui": ui}
_CITY_RE = re.compile(r"(?<![a-z])(" + "|".join(sorted(map(re.escape, CITY_ALIASES), key=len, reverse=True)) + r")(?![a-z])")


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


def _clean_text(value: Any) -> Optional[str]:
    """Drop Instagram's meta-description fallback ("12K Followers, 3 Following, ... See Instagram photos")."""
    if not value or _META_TEXT.match(str(value)) or "See Instagram photos and videos" in str(value):
        return None
    return str(value).strip() or None


def _clean_name(value: Any) -> Optional[str]:
    return clean_display_name(value)


def _own_words(profile: Dict[str, Any], posts: List[Dict[str, Any]]) -> str:
    """Creator-authored text only (name, bio, captions, post locations, hashtags), never page chrome."""
    text = " ".join(str(_clean_text(profile.get(k)) or "") for k in ("display_name", "bio_text", "location_text"))
    return text + " " + str(profile.get("handle") or "") + " " + " ".join(
        f"{post.get('caption_text') or ''} {post.get('location_text') or ''} {' '.join(post.get('hashtags') or [])}"
        for post in posts
    )


def _city(profile: Dict[str, Any], posts: List[Dict[str, Any]]) -> Optional[str]:
    """Most-mentioned Indian city across bio, location and posts (hashtags like #punefood count)."""
    text = _own_words(profile, posts).lower()
    counts: Counter = Counter()
    for match in _CITY_RE.findall(text):
        counts[CITY_ALIASES[match]] += 1
    for tag in re.findall(r"#([a-z0-9_]+)", text):
        for key, name in CITY_ALIASES.items():
            if " " not in key and len(key) >= 4 and key in tag:
                counts[name] += 1
                break
    bio = " ".join(str(profile.get(k) or "") for k in ("bio_text", "location_text")).lower()
    for match in _CITY_RE.findall(bio):
        counts[CITY_ALIASES[match]] += 3  # the creator's own statement weighs more
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
    )
    if not rates:
        return feature.get("engagement_rate")
    middle = len(rates) // 2
    return rates[middle] if len(rates) % 2 else (rates[middle - 1] + rates[middle]) / 2


def _india(profile: Dict[str, Any], posts: List[Dict[str, Any]]) -> List[str]:
    # Stored language_signals on older captures came from the whole page (footer included); ignore them.
    from .eligibility import india_evidence

    return india_evidence(_own_words(profile, posts))


def _languages(profile: Dict[str, Any], posts: List[Dict[str, Any]]) -> List[str]:
    from .e2e import _language_signals

    return _language_signals(_own_words(profile, posts))
