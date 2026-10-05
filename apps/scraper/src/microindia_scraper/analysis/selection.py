"""Which reels to analyse for a creator, and the ``media.reel`` follow-ups that fetch them.

Rule (owner decision, 6 Oct): at least 15 reels per kept creator, chosen as
- about 8 most recent (by media id, which starts with a timestamp),
- the 4 best performers against the creator's own median (likes + comments),
- every sponsored or collab reel (paid-partnership label, disclosure in the caption, posted with/under
  another account),
- topped up with the next most recent until 15 (or all known reels when fewer exist).

Known reels come from every capture of the creator (``content_snapshots``), so the pool grows as the
creator is re-scraped.
"""

from __future__ import annotations

import json
import sqlite3
import time
from statistics import median
from typing import Any, Dict, List, Optional

from ..brand import DISCLOSURE_RE
from ..runtime.results import FollowUp
from .media import owner_of_permalink, shortcode_of, shortcode_to_pk

TARGET = 15
RECENT = 8
TOP = 4
MEDIA_PRIORITY = -10          # media.reel runs on its own worker; priority only orders media tasks
BRAND_READY_BONUS = 4         # brand-ready creators first
# Within a priority, tasks run oldest first (FIFO). Creators enqueue their reels together, so they still
# complete roughly one at a time. (A LIFO trick mirroring run_at starved retried tasks; removed 6 Oct.)


def known_reels(connection: sqlite3.Connection, username: str) -> List[Dict[str, Any]]:
    """Every reel seen in the creator's captures, newest observation per reel, newest first."""
    username = username.lower().lstrip("@")
    rows = connection.execute(
        """SELECT pc.captured_at, cs.content_index, cs.payload
           FROM profile_captures pc JOIN content_snapshots cs ON cs.capture_id = pc.capture_id
           WHERE pc.profile_url = ? OR pc.candidate_key = ?
           ORDER BY pc.captured_at DESC, cs.content_index ASC""",
        (f"https://www.instagram.com/{username}/", f"instagram:{username}"),
    ).fetchall()
    reels: Dict[str, Dict[str, Any]] = {}
    for order, row in enumerate(rows):
        post = json.loads(row[2])
        if post.get("content_type") != "reel" or not post.get("permalink"):
            continue
        try:
            code = shortcode_of(post["permalink"])
        except ValueError:
            continue
        if code in reels:
            continue
        owner = owner_of_permalink(post["permalink"])
        likes, comments = post.get("like_count"), post.get("comment_count")
        caption = str(post.get("caption_text") or "")
        reels[code] = {
            "shortcode": code,
            "permalink": post["permalink"],
            "owner": owner,
            "order": order,  # grid order across captures: newest capture first, then grid position
            "pinned": bool(post.get("is_pinned")),
            "published_at": post.get("published_at"),
            "likes": likes,
            "comments": comments,
            "engagement": (likes or 0) + (comments or 0) if likes is not None else None,
            "paid_partnership": bool(post.get("is_paid_partnership")),
            "disclosed": bool(DISCLOSURE_RE.search(caption)),
            "other_owner": bool(owner and owner != username),
        }
    _apply_media_flags(connection, username, reels)
    items = list(reels.values())
    # Newest first by media pk: Instagram ids start with a millisecond timestamp, so this is true
    # recency even when published_at is missing and pinned reels (often old) sit on top of the grid.
    items.sort(key=lambda item: -shortcode_to_pk(item["shortcode"]))
    return items


def _apply_media_flags(connection: sqlite3.Connection, username: str, reels: Dict[str, Dict[str, Any]]) -> None:
    """Instagram's own flags from ``reel_media`` (paid-partnership label, sponsor tags, co-authors) beat captions."""
    try:
        rows = connection.execute(
            """SELECT shortcode, is_paid_partnership, sponsor_tags, coauthors, play_count FROM reel_media
               WHERE creator = ? ORDER BY fetched_at""", (username,)).fetchall()
    except sqlite3.OperationalError:  # insight tables not created yet
        return
    for code, paid, sponsors, coauthors, plays in rows:
        item = reels.get(code)
        if item is None:
            continue
        item["paid_partnership"] = item["paid_partnership"] or bool(paid) or bool(json.loads(sponsors or "[]"))
        item["other_owner"] = item["other_owner"] or bool([c for c in json.loads(coauthors or "[]") if c != username])
        item["plays"] = plays


def select_reels(connection: sqlite3.Connection, username: str, *, target: int = TARGET) -> List[Dict[str, Any]]:
    reels = known_reels(connection, username)
    chosen: Dict[str, List[str]] = {}

    def take(item: Dict[str, Any], reason: str) -> None:
        chosen.setdefault(item["shortcode"], [])
        if reason not in chosen[item["shortcode"]]:
            chosen[item["shortcode"]].append(reason)

    for item in reels[:RECENT]:
        take(item, "recent")
    scored = [item for item in reels if item["engagement"] is not None]
    base = median([item["engagement"] for item in scored]) if scored else None
    if base:
        for item in scored:
            item["vs_median"] = round(item["engagement"] / base, 2)
        for item in sorted(scored, key=lambda item: item["engagement"], reverse=True)[:TOP]:
            take(item, "top_performer")
    for item in reels:
        if item["paid_partnership"] or item["disclosed"]:
            take(item, "sponsored")
        if item["other_owner"]:
            take(item, "collab")
    for item in reels:
        if len(chosen) >= target:
            break
        if item["shortcode"] not in chosen:
            take(item, "recent_topup")
    by_code = {item["shortcode"]: item for item in reels}
    return [{**by_code[code], "reasons": reasons, "creator_median_engagement": base} for code, reasons in chosen.items()]


def reel_follow_ups(database: str, username: str, *, brand_ready: bool = False, target: int = TARGET,
                    now: Optional[float] = None) -> List[FollowUp]:
    """``media.reel`` tasks for a kept creator's selected reels. Dedupe is by shortcode (task key)."""
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=10)
    try:
        selected = select_reels(connection, username, target=target)
    finally:
        connection.close()
    priority = MEDIA_PRIORITY + (BRAND_READY_BONUS if brand_ready else 0)
    return [
        FollowUp(
            kind="media.reel",
            key=item["shortcode"],
            payload={"shortcode": item["shortcode"], "username": username.lower(), "permalink": item["permalink"],
                     "reasons": item["reasons"]},
            priority=priority,
        )
        for item in selected
    ]
