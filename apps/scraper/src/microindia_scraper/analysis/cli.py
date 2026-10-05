"""``python -m microindia_scraper.analysis <command>``

    reel <permalink|shortcode> [--force] [--creator HANDLE]   fetch (if needed) + analyse one reel, print JSON
    creator <handle> [--force] [--max-new N]                   select reels, analyse what's missing, build dossier
    eval [reels|creators|businesses|all] [--no-run]           score against data/evals/golden_*.json
    local-search "<task>" [--place …]                          Local finder (Phase 5)

Only the reel/creator commands touch the browser, and only when a reel has no cached media: they attach a
new tab to the signed-in Chrome on :9222 (never the workers' tabs) and close it when done.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional

from .. import llm
from . import db
from .media import paths_for, shortcode_of

CDP_URL = os.environ.get("MICROINDIA_CDP_URL", "http://127.0.0.1:9222")


def _err(**event: Any) -> None:
    print(json.dumps({"ts": round(time.time(), 1), **event}, ensure_ascii=False, default=str), file=sys.stderr, flush=True)


class LazyTab:
    """Opens one tab on the signed-in Chrome the first time it is needed; closes it on exit."""

    def __init__(self, database: str) -> None:
        self.database = database
        self.session = None
        self.tab = None

    async def get(self) -> Any:
        if self.tab is None:
            from ..runtime.browser import BrowserSession
            from ..runtime.pacing import Pacer

            self.session = BrowserSession(CDP_URL, pacer=Pacer(self.database))
            # Attach directly: BrowserSession.start() may sweep "unresponsive" tabs on failure, and this
            # one-off command must never touch the workers' tabs.
            await self.session._attach()
            self.tab = await self.session.new_page()
            _err(event="tab_opened")
        return self.tab

    async def close(self) -> None:
        if self.tab is not None:
            try:
                await self.tab.close()
            except Exception:
                pass
        if self.session is not None:
            await self.session.stop()
        if self.tab is not None:
            _err(event="tab_closed")


@asynccontextmanager
async def lazy_tab(database: str) -> AsyncIterator[LazyTab]:
    holder = LazyTab(database)
    try:
        yield holder
    finally:
        await holder.close()


def _needs_fetch(database: str, shortcode: str, *, refresh: bool) -> bool:
    connection = db.connect(database)
    try:
        media = db.latest_media(connection, shortcode)
        assets = db.get_assets(connection, shortcode) or {}
    finally:
        connection.close()
    if refresh or media is None:
        return True
    frames = assets.get("frames_json") or []
    have_frames = bool(frames) and all(os.path.exists(frame["path"]) for frame in frames)
    return not have_frames and not os.path.exists(paths_for(database, shortcode)["video"])


async def _ensure_media(tab: LazyTab, database: str, shortcode: str, creator: Optional[str], *, refresh: bool) -> Optional[str]:
    """Returns a reason string when the reel can't be analysed."""
    if not _needs_fetch(database, shortcode, refresh=refresh):
        return None
    from .fetch import fetch_reel

    from ..runtime.pacing import Throttled

    page = await tab.get()
    try:
        result = await fetch_reel(page, database, shortcode, creator=creator, download_video=True)
    except Throttled as exc:
        return f"instagram is rate-limiting: {exc}"
    _err(event="media_fetched", shortcode=shortcode, status=result["status"], seconds=result.get("seconds"))
    return None if result["status"] == "ok" else f"{result['status']}: {result.get('reason')}"


def _media_facts(media: Dict[str, Any]) -> Dict[str, Any]:
    audio = media.get("audio_json") or {}
    return {
        "shortcode": media.get("shortcode"), "owner": media.get("owner_username"), "creator": media.get("creator"),
        "posted": media.get("taken_at"), "duration": media.get("duration"), "plays": media.get("play_count"),
        "likes": media.get("like_count"), "comments": media.get("comment_count"),
        "audio": {"type": audio.get("type"), "title": audio.get("title"), "artist": audio.get("artist")},
        "paid_partnership": media.get("is_paid_partnership"), "sponsor_tags": media.get("sponsor_tags"),
        "coauthors": media.get("coauthors"), "usertags": media.get("usertags"), "location": media.get("location"),
    }


def _transcript_facts(transcript: Dict[str, Any]) -> Dict[str, Any]:
    speech = [segment for segment in transcript.get("segments") or [] if segment.get("speech")]
    return {"language": transcript.get("language"), "speech_detected": transcript.get("speech_detected"),
            "speech_seconds": transcript.get("speech_seconds"),
            "first_lines": [f"[{s['start']:.1f}s] {s['text']}" for s in speech[:2]],
            "unsure_segments": sum(1 for s in transcript.get("segments") or [] if not s.get("speech"))}


async def cmd_reel(args: argparse.Namespace) -> Dict[str, Any]:
    from .reel import analyze

    database = llm.database_path(args.database)
    shortcode = shortcode_of(args.target)
    creator = (args.creator or "").lower().lstrip("@") or None
    async with lazy_tab(database) as tab:
        problem = await _ensure_media(tab, database, shortcode, creator, refresh=args.refresh)
    if problem:
        return {"shortcode": shortcode, "error": problem}
    outcome = await asyncio.to_thread(analyze, database, shortcode, force=args.force, creator=creator,
                                      delete_video=not args.keep_video)
    return {
        "media": _media_facts(outcome["media"]),
        "transcript": _transcript_facts(outcome["transcript"]),
        "analysis": outcome["result"],
        "frames_dir": paths_for(database, shortcode)["frames"],
        "meta": {**outcome["meta"], "analysis_id": outcome["analysis_id"]},
    }


async def cmd_creator(args: argparse.Namespace) -> Dict[str, Any]:
    from .creator import build_dossier
    from .reel import MissingInput, analyze
    from .selection import select_reels
    from .spec import load

    database = llm.database_path(args.database)
    handle = args.handle.lower().lstrip("@")
    connection = db.connect(database)
    try:
        selected = select_reels(connection, handle)
        version = load("reel").version
        done = {item["shortcode"] for item in selected if db.latest_analysis(connection, item["shortcode"], version)}
    finally:
        connection.close()
    if not selected:
        return {"creator": handle, "error": "no captured reels for this handle (is it a kept creator?)"}
    _err(event="selected", creator=handle, reels=len(selected), already_analysed=len(done))
    todo = [item for item in selected if args.force or item["shortcode"] not in done][: args.max_new or None]
    statuses: Dict[str, str] = {code: "cached" for code in done}
    semaphore = asyncio.Semaphore(2)
    jobs: List[asyncio.Task] = []

    async def analyse(code: str) -> None:
        async with semaphore:
            try:
                outcome = await asyncio.to_thread(analyze, database, code, force=args.force, creator=handle)
                statuses[code] = "cached" if outcome["meta"].get("cached") else "analysed"
                _err(event="reel_done", shortcode=code, seconds=outcome["meta"].get("seconds"),
                     timings=outcome["meta"].get("timings"))
            except (MissingInput, llm.LLMError, RuntimeError) as exc:
                statuses[code] = f"failed: {exc}"
                _err(event="reel_failed", shortcode=code, error=str(exc))

    async with lazy_tab(database) as tab:
        for item in todo:
            code = item["shortcode"]
            try:
                problem = await _ensure_media(tab, database, code, handle, refresh=False)
            except Exception as exc:  # one bad reel must not stop the creator
                problem = f"fetch failed: {exc!r}"
            if problem:
                statuses[code] = problem
                _err(event="reel_skipped", shortcode=code, reason=problem)
                continue
            jobs.append(asyncio.create_task(analyse(code)))
        await asyncio.gather(*jobs)
    outcome = await asyncio.to_thread(build_dossier, database, handle, force=args.force)
    return {
        "creator": handle,
        "selected": [{"shortcode": item["shortcode"], "reasons": item["reasons"], "status": statuses.get(item["shortcode"], "skipped")}
                     for item in selected],
        "dossier": outcome.get("result"),
        "stats": outcome.get("stats"),
        "meta": outcome.get("meta") or {"status": outcome.get("status"), "analysed": outcome.get("analysed")},
    }


def cmd_eval(args: argparse.Namespace) -> Dict[str, Any]:
    from .evals import run

    result = run(llm.database_path(args.database), args.which, run_missing=not args.no_run)
    for name, report in result["sets"].items():
        if report.get("status") != "ok":
            _err(event="eval_set", set=name, status=report["status"], message=report.get("message"))
    for report in result["sets"].values():
        report.pop("row_details", None)  # kept in the run file, too long for the terminal
    return result


def cmd_local_search(args: argparse.Namespace) -> Dict[str, Any]:
    return {"status": "not_built",
            "message": "The Local finder is Phase 5: business cards (analyze.business) and source.local are not built yet.",
            "task": " ".join(args.task), "place": args.place}


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m microindia_scraper.analysis", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", default=None, help="SQLite path (default: data/microindia.sqlite3)")
    sub = parser.add_subparsers(dest="command", required=True)
    reel = sub.add_parser("reel", help="analyse one reel")
    reel.add_argument("target", help="reel permalink or shortcode")
    reel.add_argument("--force", action="store_true", help="skip the model cache and re-run with the current prompt")
    reel.add_argument("--refresh", action="store_true", help="re-fetch the media JSON (and video) from Instagram")
    reel.add_argument("--creator", help="creator handle when the reel is not in our captures")
    reel.add_argument("--keep-video", action="store_true", help="don't delete the mp4 afterwards")
    creator = sub.add_parser("creator", help="build one creator's dossier")
    creator.add_argument("handle")
    creator.add_argument("--force", action="store_true", help="re-run every model call")
    creator.add_argument("--max-new", type=int, default=0, help="analyse at most N new reels (0 = all selected)")
    evaluate = sub.add_parser("eval", help="score against golden sets")
    evaluate.add_argument("which", nargs="?", default="all", choices=["reels", "creators", "businesses", "all"])
    evaluate.add_argument("--no-run", action="store_true", help="score existing analyses only, never call the model")
    local = sub.add_parser("local-search", help="Local finder (Phase 5)")
    local.add_argument("task", nargs="*")
    local.add_argument("--place")
    args = parser.parse_args(argv)
    if args.command == "reel":
        result = asyncio.run(cmd_reel(args))
    elif args.command == "creator":
        result = asyncio.run(cmd_creator(args))
    elif args.command == "eval":
        result = cmd_eval(args)
    else:
        result = cmd_local_search(args)
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
