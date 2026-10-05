"""``media.reel``: a reel's media JSON from the signed-in tab, then its mp4 to ``data/media/video/``.

Endpoint (checked 6 Oct 2026): ``GET https://www.instagram.com/api/v1/media/<pk>/info/`` with the web app
id and session cookies (``runtime.page.fetch_json``) returns ``{"items": [item], "status": "ok"}`` where the
item has ``code``, ``pk``, ``media_type`` (2 = video), ``product_type`` ("clips"), ``taken_at`` (unix),
``video_duration``, ``play_count`` / ``ig_play_count`` / ``fb_play_count`` (``view_count`` is null),
``like_count``, ``comment_count``, ``has_audio``, ``video_versions`` [{url, width, height, bandwidth}],
``image_versions2.candidates`` [{url, width, height}], ``clips_metadata`` (``audio_type`` "licensed_music"
with ``music_info.music_asset_info`` {title, display_artist} or "original_sounds" with
``original_sound_info`` {original_audio_title, ig_artist.username}), ``is_paid_partnership``,
``sponsor_tags``, ``coauthor_producers``, ``usertags.in``, ``location``, ``caption.text``,
``original_lang_for_translations`` and ``user`` (owner).

The ``video_versions`` URL is a signed CDN link that downloads with a plain HTTP GET (no cookies); the
progressive mp4 carries both h264 video and aac audio. If the plain GET fails, the file is fetched from
inside the signed-in page instead.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..runtime import Done, Fail, FollowUp, Retry, Skip, TaskContext, handler
from ..runtime.page import INSTAGRAM_ORIGIN, ensure_origin, evaluate, fetch_json
from ..runtime.pacing import Throttled
from . import db
from .media import paths_for, shortcode_of, shortcode_to_pk

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/141.0.0.0 Safari/537.36")
MAX_VIDEO_BYTES = 250 * 1024 * 1024
MAX_IN_PAGE_BYTES = 60 * 1024 * 1024
MAX_DURATION_SECONDS = 15 * 60
DROP_RAW_KEYS = ("video_dash_manifest", "facepile_top_likers", "top_likers", "preview_comments",
                 "meta_ai_suggested_prompts", "mezql_token")


def media_info_url(shortcode: str) -> str:
    return f"{INSTAGRAM_ORIGIN}/api/v1/media/{shortcode_to_pk(shortcode)}/info/"


def _iso(timestamp: Any) -> Optional[str]:
    try:
        return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _usernames(entries: Any, key: Optional[str] = None) -> List[str]:
    names = []
    for entry in entries or []:
        user = entry.get(key) if key and isinstance(entry, dict) else entry
        if isinstance(user, dict) and user.get("username"):
            names.append(str(user["username"]).lower())
    return names


def audio_of(item: Dict[str, Any]) -> Dict[str, Any]:
    clips = item.get("clips_metadata") or {}
    music = ((clips.get("music_info") or {}).get("music_asset_info")) or {}
    original = clips.get("original_sound_info") or {}
    if music:
        return {"type": clips.get("audio_type") or "licensed_music", "title": music.get("title"),
                "artist": music.get("display_artist"), "id": music.get("audio_cluster_id") or music.get("id"),
                "original": False}
    if original:
        artist = (original.get("ig_artist") or {}).get("username")
        return {"type": clips.get("audio_type") or "original_sounds", "title": original.get("original_audio_title"),
                "artist": artist, "id": original.get("audio_asset_id"), "original": True}
    return {"type": clips.get("audio_type"), "title": None, "artist": None, "id": None, "original": None}


def parse_media_item(item: Dict[str, Any], *, creator: Optional[str]) -> Dict[str, Any]:
    """The fields we keep from one media-info item (unknown stays None)."""
    versions = sorted(item.get("video_versions") or [], key=lambda v: (v.get("height") or 0, v.get("bandwidth") or 0),
                      reverse=True)
    covers = sorted(((item.get("image_versions2") or {}).get("candidates") or []),
                    key=lambda c: c.get("width") or 0, reverse=True)
    location = item.get("location") or {}
    owner = (item.get("user") or item.get("owner") or {}).get("username")
    raw = {key: value for key, value in item.items() if key not in DROP_RAW_KEYS}
    caption = item.get("caption") or {}
    return {
        "shortcode": item.get("code"),
        "pk": str(item.get("pk") or ""),
        "owner_username": owner.lower() if owner else None,
        "creator": (creator or owner or "").lower() or None,
        "taken_at": _iso(item.get("taken_at")),
        "duration": item.get("video_duration"),
        "play_count": item.get("play_count"),
        "ig_play_count": item.get("ig_play_count"),
        "view_count": item.get("view_count"),
        "like_count": None if item.get("like_and_view_counts_disabled") else item.get("like_count"),
        "comment_count": item.get("comment_count"),
        "has_audio": item.get("has_audio"),
        "audio_type": (item.get("clips_metadata") or {}).get("audio_type"),
        "audio_json": audio_of(item),
        "cover_url": covers[0]["url"] if covers else None,
        "video_url": versions[0]["url"] if versions else None,
        "width": item.get("original_width"),
        "height": item.get("original_height"),
        "is_paid_partnership": item.get("is_paid_partnership"),
        "sponsor_tags": _usernames(item.get("sponsor_tags"), "sponsor") or _usernames(item.get("sponsor_tags")),
        "coauthors": _usernames(item.get("coauthor_producers")),
        "usertags": _usernames((item.get("usertags") or {}).get("in"), "user"),
        "location": location.get("name"),
        "caption": caption.get("text") if isinstance(caption, dict) else None,
        "caption_language": item.get("original_lang_for_translations"),
        "raw_json": raw,
        "media_type": item.get("media_type"),
    }


def download(url: str, path: str, *, max_bytes: int = MAX_VIDEO_BYTES, timeout: float = 120) -> int:
    """Plain HTTP GET of a signed CDN url to ``path`` (atomic). Returns bytes written.
    The ``.part`` file never survives a failure."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    partial = path + ".part"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": INSTAGRAM_ORIGIN + "/"})
    written = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, open(partial, "wb") as handle:
            while True:
                block = response.read(1 << 20)
                if not block:
                    break
                written += len(block)
                if written > max_bytes:
                    raise ValueError(f"video larger than {max_bytes} bytes")
                handle.write(block)
        if written == 0:
            raise ValueError("empty download")
        os.replace(partial, path)
    finally:
        if os.path.exists(partial):
            os.remove(partial)
    return written


async def download_in_page(page: Any, url: str, path: str) -> int:
    """Fallback: fetch the mp4 from inside the signed-in page and hand it over as base64."""
    await ensure_origin(page)
    result = await evaluate(page, """async (url) => {
        const response = await fetch(url, {credentials: 'include'});
        if (!response.ok) return JSON.stringify({status: response.status});
        const buffer = await response.arrayBuffer();
        if (buffer.byteLength > %d) return JSON.stringify({status: 413});
        let binary = '';
        const bytes = new Uint8Array(buffer);
        for (let i = 0; i < bytes.length; i += 32768) binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 32768));
        return JSON.stringify({status: response.status, data: btoa(binary)});
    }""" % MAX_IN_PAGE_BYTES, url)
    if not isinstance(result, dict) or not result.get("data"):
        raise ValueError(f"in-page download failed: {result.get('status') if isinstance(result, dict) else result!r}")
    data = base64.b64decode(result["data"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path + ".part", "wb") as handle:
            handle.write(data)
        os.replace(path + ".part", path)
    finally:
        if os.path.exists(path + ".part"):
            os.remove(path + ".part")
    return len(data)


SOFT_LIMIT_RE = re.compile(r"wait a few minutes|feedback_required|try again later|rate limit|spam", re.I)
GONE_RE = re.compile(r"not found|unavailable|does not exist|deleted|no longer", re.I)


def classify_response(response: Dict[str, Any]) -> str:
    """``ok`` | ``missing`` (gone for good: Skip) | ``throttled`` (Instagram's soft limit: back off, refund)
    | ``transient`` (5xx, network, odd 4xx: retry)."""
    status = int(response.get("status") or 0)
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    message = " ".join(str(value) for value in (data.get("message"), data.get("feedback_title"), data.get("spam"),
                                                 response.get("text", "")[:300]) if value)
    if status == 200 and data.get("items"):
        return "ok"
    if status == 429 or SOFT_LIMIT_RE.search(message):
        return "throttled"
    if status == 404 or (status == 400 and GONE_RE.search(message)):
        return "missing"
    if status == 200 and data.get("status") == "ok" and data.get("items") == []:
        return "missing"
    return "transient"


async def fetch_reel(page: Any, database: str, shortcode: str, *, creator: Optional[str] = None,
                     download_video: bool = True) -> Dict[str, Any]:
    """Fetch media JSON (always, metrics change) and the mp4 (when ``download_video``).

    Returns ``{"status": "ok"|"missing"|"transient"|"not_video"|"too_long"|"disk_hold", "media", "video_path",
    "seconds"}``; raises ``Throttled`` on Instagram's soft limits ("wait a few minutes", feedback_required).
    """
    started = time.time()
    response = await fetch_json(page, media_info_url(shortcode))
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    kind = classify_response(response)
    if kind != "ok":
        message = data.get("message") or response.get("text", "")[:120]
        if kind == "throttled":
            # fetch_json recorded this soft limit as an ordinary response; tell the shared pacer it was a
            # 429 so every runner slows down and cools off, not just this task.
            pacer = getattr(page, "pacer", None)
            if pacer is not None:
                pacer.record(429)
            raise Throttled(f"{response.get('status')}: {message}")
        return {"status": kind, "reason": f"{response.get('status')}: {message}", "media": None}
    media = parse_media_item(data["items"][0], creator=creator)
    media["shortcode"] = media["shortcode"] or shortcode
    if media["shortcode"] != shortcode:
        media["requested_shortcode"] = shortcode
    fetch_seconds = time.time() - started
    connection = db.connect(database)
    try:
        media_type = media.pop("media_type", None)
        requested = media.pop("requested_shortcode", None)
        media["shortcode"] = shortcode  # keep our key (long share codes resolve to the short code)
        if requested:
            media["raw_json"]["requested_shortcode"] = requested
        db.save_media(connection, media)
        if media_type != 2 or not media.get("video_url"):
            return {"status": "not_video", "reason": f"media_type {media_type}", "media": media}
        if (media.get("duration") or 0) > MAX_DURATION_SECONDS:
            return {"status": "too_long", "reason": f"{media['duration']:.0f}s video", "media": media}
        video_path = None
        download_seconds = 0.0
        size = None
        if download_video:
            from .ops import disk_hold_reason

            hold = disk_hold_reason(database)
            if hold:
                return {"status": "disk_hold", "reason": hold, "media": media}
            video_path = paths_for(database, shortcode)["video"]
            began = time.time()
            try:
                size = await asyncio.to_thread(download, media["video_url"], video_path)
            except (urllib.error.URLError, OSError, ValueError):
                size = await download_in_page(page, media["video_url"], video_path)
            download_seconds = time.time() - began
        timing = {"media_json": round(fetch_seconds, 2), "download": round(download_seconds, 2)}
        assets = db.get_assets(connection, shortcode) or {}
        db.save_assets(connection, shortcode, creator=media.get("creator"), video_path=video_path or assets.get("video_path"),
                       video_bytes=size or assets.get("video_bytes"),
                       seconds_json={**(assets.get("seconds_json") or {}), **timing})
    finally:
        connection.close()
    return {"status": "ok", "media": media, "video_path": video_path, "seconds": timing}


def has_current_analysis(database: str, shortcode: str) -> bool:
    from .spec import load

    connection = db.connect(database)
    try:
        return db.latest_analysis(connection, shortcode, load("reel").version) is not None
    finally:
        connection.close()


@handler("media.reel", needs_page=True, timeout_seconds=420)
async def media_reel(ctx: TaskContext, task: Dict[str, Any]):
    from .ops import media_hold_reason, refunded_retry

    payload = task["payload"]
    try:
        shortcode = shortcode_of(payload.get("shortcode") or task["key"])
    except ValueError as exc:
        return Fail(str(exc))
    hold = media_hold_reason(ctx.tasks, ctx.database) if ctx.tasks is not None else None
    if hold:  # the gate normally holds the kind; this catches a task leased just before it closed
        return refunded_retry(task, f"held: {hold}", 900)
    creator = (payload.get("username") or "").lower() or None
    analysed = not payload.get("force") and await asyncio.to_thread(has_current_analysis, ctx.database, shortcode)
    try:
        result = await fetch_reel(ctx.page, ctx.database, shortcode, creator=creator, download_video=not analysed)
    except Throttled as exc:
        # Instagram's soft limit is not this reel's fault. (Returned, not raised: the attempt refund
        # must be made on the task dict that TaskStore.apply writes.)
        return refunded_retry(task, f"throttled: {exc}", 300)
    status = result["status"]
    if status == "missing":
        return Skip(f"media not available ({result.get('reason')})")
    if status == "transient":
        return Retry(f"instagram: {result.get('reason')}")
    if status == "disk_hold":
        return refunded_retry(task, f"held: {result.get('reason')}", 1800)
    if status in ("not_video", "too_long"):
        return Skip(f"not analysable: {result.get('reason')}")
    media = result["media"]
    data = {"shortcode": shortcode, "plays": media.get("play_count"), "duration": media.get("duration"),
            "paid_partnership": media.get("is_paid_partnership"), "seconds": result.get("seconds"),
            "refreshed_only": bool(analysed)}
    if analysed:
        return Done(data)  # metrics refreshed; the current prompt version already analysed this reel
    return Done(data, follow_ups=[FollowUp("analyze.reel", shortcode, {"shortcode": shortcode, "username": creator},
                                           priority=int(task.get("priority") or 0))])
