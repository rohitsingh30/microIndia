"""Scraper task kinds.

``scrape.profile`` runs the existing full capture (profile page, then up to 18
post pages for in-band profiles) on a borrowed tab. An eligible creator feeds
sourcing: its similar accounts and every account it mentions get queued.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from ..runtime import AuthBlocked, Done, Fail, FollowUp, Retry, Skip, TaskContext, TaskStore, handler
from ..brand import brand_signals
from ..store import CaptureStore
from .common import mentioned_usernames, profile_scrape, username_of

REFRESH_AFTER_SECONDS = 24 * 60 * 60
ELIGIBLE_STATUSES = {"complete", "partial"}


@handler("scrape.profile", timeout_seconds=900)
async def scrape_profile(ctx: TaskContext, task: Dict[str, Any]):
    from ..e2e import run as capture_profile

    payload = task["payload"]
    username = username_of(payload.get("username") or task["key"])
    if not username:
        return Fail(f"not an Instagram username: {payload.get('username') or task['key']!r}")
    result = await capture_profile(
        f"https://www.instagram.com/{username}/",
        None,
        "Default",
        ctx.database,
        browser_session=ctx.browser,
        page=ctx.page,
        requested_niche=payload.get("niche"),
    )
    status = str(result.get("status") or "")
    if result.get("evidence") == "MANUAL_AUTH_RECHECK":
        # Confirm before pausing everything: a slow page after a network blip can look like a login wall.
        from ..runtime.page import INSTAGRAM_ORIGIN, open_url, page_shows_login
        from ..runtime.results import AuthRequired

        try:
            await open_url(ctx.page, INSTAGRAM_ORIGIN + "/")
            really_blocked = await page_shows_login(ctx.page)
        except AuthRequired:
            really_blocked = True
        if really_blocked:
            return AuthBlocked(f"profile page for {username} showed a login wall (confirmed on home page)")
        return Retry("login wall on profile page but session is fine; retrying later", after_seconds=300)
    gate = result.get("quality_gate") or {}
    profile, content = _saved_capture(ctx.database, result.get("capture_id"))
    data = {
        "capture_id": result.get("capture_id"),
        "status": status,
        # "eligible" = in scope (not private, not clearly outside the band, not a business).
        # Every in-scope profile is fully captured and kept; fit is decided on the Find page.
        "eligible": status in ELIGIBLE_STATUSES,
        "reasons": gate.get("reasons") or ([result["evidence"]] if result.get("evidence") else []),
        "followers": profile.get("follower_count"),
        "notes": gate.get("notes") or [],
        "india": _india_from(profile, content),
        "category": _niche_from(profile, content, payload),
        **brand_signals(profile, content),
    }
    page_text = " ".join(str(profile.get(k) or "") for k in ("account_type", "display_name", "bio_text"))
    if data["followers"] is None and "page isn't available" in page_text.replace("’", "'"):
        return Skip("profile not available (deleted, renamed or restricted)", data)
    if not data["eligible"] and data["followers"] is None and not any("private" in r for r in data["reasons"]):
        # The header hadn't rendered when we read it: try again later instead of losing the profile.
        return Retry("follower count not readable yet", after_seconds=600)
    if not data["eligible"]:
        return Skip("; ".join(data["reasons"][:3]) or status or "not eligible", data)
    from .sourcing import FOCUS_PRIORITY, breadth_priority, current_focus, in_focus, niche_coverage

    # Depth: dig hardest around creators in the current random focus (niche · size), eagerly in
    # under-covered niches, lazily in niches that already have plenty.
    if in_focus(current_focus(ctx.tasks), data["category"], data["followers"]):
        depth = FOCUS_PRIORITY - 1
    else:
        depth = breadth_priority(data["category"], niche_coverage(ctx.tasks), wide=5, deep=2) if data["category"] else 3
    follow_ups = _follow_ups(username, profile, content, payload, brand_active=data["brand_ready"], depth=depth)
    follow_ups += await asyncio.to_thread(_reel_follow_ups, ctx, username, bool(data["brand_ready"]))
    return Done(data, follow_ups=follow_ups)


def _reel_follow_ups(ctx: TaskContext, username: str, brand_ready: bool) -> list:
    """``media.reel`` for the kept creator's selected reels (≥15: recent, best, sponsored/collab). They run on
    the media worker, gated by the analyzer backlog and disk (analysis/ops.py). Never fails the scrape."""
    try:
        from ..analysis.selection import reel_follow_ups

        return reel_follow_ups(ctx.database, username, brand_ready=brand_ready)
    except Exception as exc:  # selection is read-only and optional; a bug here must not cost a profile
        ctx.log(event="reel_selection_failed", username=username, error=repr(exc))
        return []


def _niche_from(profile: Dict[str, Any], content: list, payload: Dict[str, Any]) -> Any:
    """Own words first; then Instagram's "Home Chef"-style hint; then the niche of whoever led us here."""
    from ..profile_text import _own_words
    from ..niches import NICHES, primary_niche

    niche = primary_niche(_own_words(profile, content) + " " + str(payload.get("hint") or ""))
    inherited = payload.get("niche")
    return niche or (inherited if inherited in NICHES else None)


def _india_from(profile: Dict[str, Any], content: list) -> list:
    from ..profile_text import _india

    return _india(profile, content)


def _saved_capture(database: str, capture_id: Any) -> tuple:
    if not capture_id:
        return {}, []
    store = CaptureStore(database)
    try:
        return store.get_profile_payload(capture_id) or {}, store.get_content_payloads(capture_id)
    finally:
        store.close()


def _follow_ups(username: str, profile: Dict[str, Any], content: list, payload: Dict[str, Any],
                brand_active: bool = False, depth: int = 4) -> list:
    texts = [profile.get("bio_text")] + [item.get("mentions") or [] for item in content]
    texts += [item.get("caption_text") for item in content]
    mentions = mentioned_usernames(texts, exclude=[username])
    # Creators already doing brand deals tend to sit next to others who do: expand them first.
    follow_ups = [FollowUp("source.similar", username, {"username": username}, priority=depth + (1 if brand_active else 0))]
    follow_ups += [
        profile_scrape(name, priority=depth, niche=payload.get("niche"), found_via=f"mention:{username}")
        for name in mentions
    ]
    return follow_ups


def refresh_eligible(tasks: TaskStore) -> int:
    """Re-scrape eligible creators once their snapshot is a day old."""
    return tasks.revisit_done(
        "scrape.profile",
        older_than_seconds=REFRESH_AFTER_SECONDS,
        where_json="json_extract(result, '$.eligible') = 1",
    )
