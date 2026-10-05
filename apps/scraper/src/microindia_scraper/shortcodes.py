"""Instagram shortcodes: pure arithmetic, importable anywhere (API, insights, analysis).

A shortcode is the media pk in base64 (URL alphabet), and the pk's high bits are the creation time,
so a post's date can be read from its permalink even when the page didn't show one.
"""

from __future__ import annotations

import re
from typing import Optional

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_SHORTCODE_RE = re.compile(r"instagram\.com/(?:[A-Za-z0-9._]+/)?(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)")


def shortcode_to_pk(shortcode: str) -> int:
    """Instagram shortcodes are the media pk in base64 (URL alphabet). Long share codes carry the pk
    in their first 11 characters."""
    value = 0
    for char in shortcode[:11]:
        index = ALPHABET.find(char)
        if index < 0:
            raise ValueError(f"not an Instagram shortcode: {shortcode!r}")
        value = value * 64 + index
    return value


INSTAGRAM_EPOCH_MS = 1314220021721  # media ids start with milliseconds since 2011-08-24 21:07:01.721 UTC


def shortcode_timestamp(shortcode: str) -> float:
    """When the media was created (unix seconds), read from its id; within minutes of ``taken_at``."""
    return ((shortcode_to_pk(shortcode) >> 23) + INSTAGRAM_EPOCH_MS) / 1000.0


def pk_to_shortcode(pk: int) -> str:
    pk = int(pk)
    if pk <= 0:
        raise ValueError("media pk must be positive")
    chars = []
    while pk:
        pk, rest = divmod(pk, 64)
        chars.append(ALPHABET[rest])
    return "".join(reversed(chars))


def shortcode_of(value: str) -> str:
    """A shortcode from a permalink (any /reel/, /p/, /tv/ form) or a bare shortcode."""
    value = (value or "").strip()
    match = _SHORTCODE_RE.search(value)
    code = match.group(1) if match else value.strip("/").split("/")[-1]
    if not code or any(char not in ALPHABET for char in code):
        raise ValueError(f"not a reel permalink or shortcode: {value!r}")
    # Long share codes ("Dc_KCGyppYEMsDerMy…", 39 chars) are the 11-char shortcode plus a share token;
    # keep one key per reel. (11 chars hold ids up to 64**11, far beyond today's ~4e18.)
    return code[:11] if len(code) > 11 else code


def permalink_timestamp(permalink: Optional[str]) -> Optional[float]:
    """Creation time (unix seconds) of the post behind a permalink, or None if it isn't one."""
    match = _SHORTCODE_RE.search(permalink or "")
    if not match:  # a profile URL or anything else: no post id to read
        return None
    try:
        return shortcode_timestamp(shortcode_of(match.group(0)))
    except (ValueError, TypeError):
        return None
