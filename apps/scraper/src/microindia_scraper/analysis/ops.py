"""Keeping the insight pipeline bounded: media backpressure, disk stops, mp4 sweeps, dossier sweeps.

- ``media_hold_reason``: ``media.reel`` waits while the analyzer is behind (more than
  ``MEDIA_ANALYZE_BACKLOG`` ``analyze.reel`` tasks queued or leased), while ``data/media/video`` holds
  more than ``MAX_VIDEO_BYTES``, or while the disk has less than ``MIN_FREE_BYTES`` free. ``run.py``'s
  gate holds the kind; the handler checks again before downloading.
- ``sweep_videos``: deletes mp4s older than a day that no queued or leased ``analyze.reel`` will read,
  and stray ``.part`` downloads.
- ``queue_due_dossiers``: queues ``analyze.creator`` for creators whose dossier is due (see
  ``creator.dossier_status``) and who have no reel still being fetched or analysed. It backs up the
  in-handler fast path, which misses when sibling reels finish together or the last reel is skipped.
- ``refunded_retry``: a retry that gives the attempt back (Claude or Instagram unavailable, disk full):
  nothing is ever marked failed for a reason that isn't the task's own.
"""

from __future__ import annotations

import os
import random
import shutil
import time
from typing import Any, Callable, Dict, List, Optional

from ..runtime.results import Retry
from .media import media_root

MEDIA_ANALYZE_BACKLOG = int(os.environ.get("MICROINDIA_MEDIA_BACKLOG", "30"))
MAX_VIDEO_BYTES = int(float(os.environ.get("MICROINDIA_MAX_VIDEO_GB", "2")) * 1024 ** 3)
MIN_FREE_BYTES = int(float(os.environ.get("MICROINDIA_MIN_FREE_GB", "20")) * 1024 ** 3)
VIDEO_MAX_AGE_SECONDS = 24 * 3600
PART_MAX_AGE_SECONDS = 3600
DOSSIER_SWEEP_SECONDS = 600
VIDEO_SWEEP_SECONDS = 3600
HOLD_PRIORITY = 10 ** 9  # a priority floor no task reaches: the kind is held
_HOLD_CACHE: Dict[str, Any] = {}


def database_of(tasks: Any) -> str:
    """The file behind a TaskStore (schedulers only get the store)."""
    row = tasks.connection.execute("PRAGMA database_list").fetchone()
    return row[2]


def refunded_retry(task: Dict[str, Any], reason: str, after_seconds: float) -> Retry:
    """A Retry that doesn't count as an attempt. ``TaskStore.apply`` writes ``task['attempts']``, so
    the refund is made on the task the runner will apply."""
    task["attempts"] = max(0, int(task.get("attempts") or 0) - 1)
    return Retry(reason, after_seconds=after_seconds)


def unavailable_delay(retry_after: float = 0.0) -> float:
    """15–30 min, and never before Claude's cooldown ends."""
    return max(retry_after, 900.0) + random.uniform(0, 900)


# -- disk and backlog ----------------------------------------------------------------------

def video_bytes(database: str) -> int:
    directory = os.path.join(media_root(database), "video")
    total = 0
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_file():
                    total += entry.stat().st_size
    except FileNotFoundError:
        return 0
    return total


def free_bytes(database: str) -> int:
    path = media_root(database)
    while not os.path.exists(path):
        path = os.path.dirname(path) or "."
    return shutil.disk_usage(path).free


def disk_hold_reason(database: str) -> Optional[str]:
    used = video_bytes(database)
    if used > MAX_VIDEO_BYTES:
        return f"data/media/video holds {used / 1024 ** 3:.1f} GB (limit {MAX_VIDEO_BYTES / 1024 ** 3:.0f} GB)"
    free = free_bytes(database)
    if free < MIN_FREE_BYTES:
        return f"only {free / 1024 ** 3:.1f} GB free on disk (need {MIN_FREE_BYTES / 1024 ** 3:.0f} GB)"
    return None


def media_hold_reason(tasks: Any, database: Optional[str] = None, *, cache_seconds: float = 30.0) -> Optional[str]:
    """Why ``media.reel`` must wait right now, or None. Cached briefly: every runner slot polls the gate."""
    database = database or database_of(tasks)
    cached = _HOLD_CACHE.get(database)
    if cached and time.time() - cached[0] < cache_seconds:
        return cached[1]
    reason = None
    backlog = tasks.pending(["analyze.reel"])
    if backlog > MEDIA_ANALYZE_BACKLOG:
        reason = f"analyzer is behind ({backlog} analyze.reel waiting, limit {MEDIA_ANALYZE_BACKLOG})"
    else:
        reason = disk_hold_reason(database)
    _HOLD_CACHE[database] = (time.time(), reason)
    return reason


def media_gate(database: Optional[str] = None) -> Callable[[Any], Dict[str, int]]:
    """A runner gate (``{kind: priority floor}``) that holds ``media.reel`` while ``media_hold_reason``."""
    def gate(tasks: Any) -> Dict[str, int]:
        return {"media.reel": HOLD_PRIORITY} if media_hold_reason(tasks, database) else {}

    return gate


# -- sweeps -------------------------------------------------------------------------------------

def sweep_videos(tasks: Any, *, now: Optional[float] = None, database: Optional[str] = None) -> int:
    """Delete mp4s that nothing will analyse (older than a day, no queued/leased analyze.reel) and old .part files."""
    database = database or database_of(tasks)
    current = time.time() if now is None else now
    directory = os.path.join(media_root(database), "video")
    if not os.path.isdir(directory):
        return 0
    waiting = {row[0] for row in tasks.connection.execute(
        "SELECT key FROM tasks WHERE kind = 'analyze.reel' AND state IN ('queued', 'leased')")}
    removed = 0
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        try:
            age = current - os.path.getmtime(path)
        except OSError:
            continue
        stale_part = name.endswith(".part") and age > PART_MAX_AGE_SECONDS
        orphan = name.endswith(".mp4") and age > VIDEO_MAX_AGE_SECONDS and name[:-4] not in waiting
        if stale_part or orphan:
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    return removed


def queue_due_dossiers(tasks: Any, *, limit: int = 200, database: Optional[str] = None) -> int:
    """Queue ``analyze.creator`` for every creator whose dossier is due and who has nothing pending."""
    from . import db
    from .creator import dossier_status
    from .selection import MEDIA_PRIORITY
    from .spec import load

    database = database or database_of(tasks)
    connection = db.connect(database)
    queued = 0
    try:
        creators = [row[0] for row in connection.execute(
            """SELECT creator FROM reel_analyses WHERE analysis_version = ? AND creator IS NOT NULL
               GROUP BY creator HAVING COUNT(DISTINCT shortcode) >= 3""", (load("reel").version,))]
        busy = {row[0] for row in connection.execute(
            """SELECT DISTINCT json_extract(payload, '$.username') FROM tasks
               WHERE kind IN ('media.reel', 'analyze.reel', 'analyze.creator') AND state IN ('queued', 'leased')""")}
        for creator in creators:
            if creator in busy:
                continue
            status = dossier_status(connection, creator)
            if not status["due"]:
                continue
            row = connection.execute(
                "SELECT MAX(priority) FROM tasks WHERE kind = 'analyze.reel' AND json_extract(payload, '$.username') = ?",
                (creator,)).fetchone()
            priority = (row[0] if row and row[0] is not None else MEDIA_PRIORITY) + 1
            if tasks.enqueue("analyze.creator", f"{creator}:{status['set_hash'][:16]}", {"username": creator}, priority=priority):
                queued += 1
                if queued >= limit:
                    break
    finally:
        connection.close()
    return queued


def every(seconds: float, fn: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """Run a scheduler at most once per ``seconds`` (runner housekeeping calls schedulers every ~15 s)."""
    state = {"last": 0.0}

    def scheduler(tasks: Any) -> Any:
        if time.time() - state["last"] < seconds:
            return None
        state["last"] = time.time()
        return fn(tasks)

    scheduler.__name__ = getattr(fn, "__name__", "scheduler")
    return scheduler


def analyzer_schedulers() -> List[Callable[[Any], Any]]:
    return [every(DOSSIER_SWEEP_SECONDS, queue_due_dossiers), every(VIDEO_SWEEP_SECONDS, sweep_videos)]


def remove_video(database: str, shortcode: str) -> bool:
    from .media import paths_for

    path = paths_for(database, shortcode)["video"]
    removed = False
    for candidate in (path, path + ".part"):
        try:
            os.remove(candidate)
            removed = True
        except FileNotFoundError:
            pass
    return removed


__all__ = ["media_gate", "media_hold_reason", "queue_due_dossiers", "refunded_retry", "sweep_videos",
           "analyzer_schedulers", "remove_video", "unavailable_delay"]
