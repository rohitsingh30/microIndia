"""Sourcer task kinds. Each one turns some starting point into ``scrape.profile`` tasks.

- ``source.search``: Instagram's own search for one query (India niche × city × language).
- ``source.similar``: the "similar accounts" Instagram suggests for a creator.
- ``source.list``: a file of usernames, profile URLs or NDJSON records.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from ..niches import NICHES, queries_for
from ..constants import SCRAPE_MAX_FOLLOWERS, SCRAPE_MIN_FOLLOWERS
from ..runtime import Done, Fail, Retry, Skip, TaskContext, TaskStore, handler
from ..runtime.page import INSTAGRAM_ORIGIN, fetch_json
from .common import profile_scrape, username_of

SEARCH_REVISIT_SECONDS = 7 * 24 * 60 * 60
MIN_FOLLOWERS, MAX_FOLLOWERS = SCRAPE_MIN_FOLLOWERS, SCRAPE_MAX_FOLLOWERS
THROTTLED_RETRY_SECONDS = 15 * 60
BREADTH_TARGET = 40          # captured in-scope creators per niche before that niche goes "deep"
SEARCHES_PER_NICHE = 2       # keep this many searches queued for every under-covered niche
BREADTH_BACKLOG_CAP = 1500   # pause new breadth searches while this many breadth finds wait to be scraped


def niche_coverage(tasks: TaskStore) -> Dict[str, int]:
    """Captured in-scope creators per niche (from scrape results)."""
    rows = tasks.connection.execute(
        """SELECT json_extract(result, '$.category') AS niche, COUNT(*) AS n FROM tasks
           WHERE kind='scrape.profile' AND state='done' AND json_extract(result, '$.eligible') = 1
           GROUP BY niche"""
    ).fetchall()
    return {row["niche"]: row["n"] for row in rows if row["niche"]}


def breadth_priority(niche: str, coverage: Dict[str, int], *, wide: int, deep: int) -> int:
    """Wide (high) priority while a niche is under its baseline, deep (low) once it has enough."""
    have = coverage.get(niche, 0)
    if have >= BREADTH_TARGET:
        return deep
    return wide + (1 if have < BREADTH_TARGET / 4 else 0)


@handler("source.search", timeout_seconds=120)
async def source_search(ctx: TaskContext, task: Dict[str, Any]):
    query = str(task["payload"].get("query") or task["key"]).strip()
    if not query:
        return Fail("empty search query")
    response = await fetch_json(ctx.page, f"{INSTAGRAM_ORIGIN}/web/search/topsearch/?query={quote(query)}")
    if response.get("status") == 429:
        return Retry("search returned 429", after_seconds=THROTTLED_RETRY_SECONDS)
    users = [item.get("user") or item for item in (response.get("data") or {}).get("users") or []]
    # The scraper decides eligibility from the live profile; only drop what is certainly out.
    usernames = [name for name in (username_of(u.get("username")) for u in users if worth_scraping(u)) if name]
    niche = task["payload"].get("niche")
    if task["payload"].get("focus"):
        priority = FOCUS_PRIORITY
    else:
        priority = breadth_priority(niche, niche_coverage(ctx.tasks), wide=5, deep=2) if niche else 1
    return Done(
        {"query": query, "found": len(usernames), "niche": niche},
        follow_ups=[profile_scrape(name, priority=priority, niche=niche or query, found_via=f"search:{query}",
                                   focus_id=task["payload"].get("focus_id"))
                    for name in usernames],
    )


@handler("source.similar", timeout_seconds=120)
async def source_similar(ctx: TaskContext, task: Dict[str, Any]):
    username = username_of(task["payload"].get("username") or task["key"])
    if not username:
        return Fail(f"not an Instagram username: {task['key']!r}")
    user_id = await _user_id(ctx.page, username)
    if isinstance(user_id, Retry):
        return user_id
    if not user_id:
        return Skip("user id not found")
    chaining = await fetch_json(ctx.page, f"{INSTAGRAM_ORIGIN}/api/v1/discover/chaining/?target_id={user_id}")
    if chaining.get("status") == 429:
        return Retry("similar accounts endpoint returned 429", after_seconds=THROTTLED_RETRY_SECONDS)
    suggested = [item for item in (chaining.get("data") or {}).get("users") or [] if worth_scraping(item)]
    hints = {str(item.get("username") or "").lower(): item.get("social_context") for item in suggested}
    names: List[str] = [item.get("username") for item in suggested]
    usernames = list(dict.fromkeys(name for name in (username_of(value) for value in names) if name and name != username))
    if not usernames:
        return Skip(f"no similar accounts (chaining HTTP {chaining.get('status')})")
    return Done(
        {"seed": username, "found": len(usernames)},
        follow_ups=[profile_scrape(name, priority=task.get("priority") or 3, found_via=f"similar:{username}",
                                   niche=task["payload"].get("niche"), hint=hints.get(name),
                                   focus_id=task["payload"].get("focus_id"))
                    for name in usernames],
    )


async def _user_id(page: Any, username: str):
    """Numeric user id from Instagram search (the profile-info endpoint is rate-limited for this account)."""
    search = await fetch_json(page, f"{INSTAGRAM_ORIGIN}/web/search/topsearch/?query={quote(username)}")
    for item in (search.get("data") or {}).get("users") or []:
        user = item.get("user") or item
        if str(user.get("username") or "").lower() == username:
            found = user.get("pk") or user.get("pk_id") or user.get("id")
            if found:
                return str(found)
    # The web_profile_info endpoint answers 429 for this account permanently; never call it, since its
    # 429s would also throttle the shared pacer for no reason. No search match -> skip the expansion.
    return None


@handler("source.list", needs_page=False, timeout_seconds=300)
async def source_list(ctx: TaskContext, task: Dict[str, Any]):
    path = str(task["payload"].get("path") or task["key"])
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError as exc:
        return Fail(f"cannot read {path}: {exc}")
    usernames: Dict[str, Dict[str, Any]] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        record: Dict[str, Any] = {}
        if line.startswith("{"):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            value = record.get("username") or record.get("profile_url")
        else:
            value = line.split(",")[0]
        name = username_of(str(value or ""))
        if name:
            usernames.setdefault(name, record)
    return Done(
        {"path": path, "found": len(usernames)},
        follow_ups=[
            profile_scrape(name, priority=3, niche=record.get("niche"), found_via=f"list:{path}")
            for name, record in usernames.items()
        ],
    )


def worth_scraping(user: Dict[str, Any]) -> bool:
    """Cheap pre-queue filter on what search/similar already return: costs no extra page loads."""
    if user.get("is_private"):
        return False
    if user.get("aigm_account_label_info"):
        return False  # Instagram's own "AI info" label on the account
    if user.get("has_anonymous_profile_picture"):
        return False  # no profile photo: almost always a tiny or inactive account
    from ..eligibility import ai_or_repost_reason

    if ai_or_repost_reason(str(user.get("username") or ""), str(user.get("full_name") or ""), ""):
        return False  # AI personas / repost pages: not worth a page load
    followers = user.get("follower_count")
    if isinstance(followers, (int, float)) and not (MIN_FOLLOWERS <= followers <= MAX_FOLLOWERS):
        return False
    return True


LOW_WATER = 500


# ---- random exploration: pick a niche · city · size, go deep there, then move on ----------------
SIZE_BANDS = ((500, 5_000, "500–5K"), (5_000, 20_000, "5K–20K"), (20_000, 100_000, "20K–100K"), (100_000, 1_000_000, "100K–1M"))
FOCUS_SECONDS = 30 * 60      # explore one focus for at most this long
FOCUS_TARGET = 25            # ...or until this many creators in its niche were captured
BARREN_SECONDS = 15 * 60     # ...or give up early if it has found nobody at all
FOCUS_PRIORITY = 10          # above background breadth (5-6) and generic depth (2)
FOCUS_SEEDS = 8              # similar-account seed expansions per focus, in total
FOCUS_WAITING_CAP = 300      # no new seeds while this many of the focus's leads still wait


def current_focus(tasks: TaskStore) -> Optional[Dict[str, Any]]:
    row = tasks.connection.execute("SELECT value FROM runtime_flags WHERE name='focus'").fetchone()
    try:
        return json.loads(row["value"]) if row else None
    except (TypeError, ValueError):
        return None


def in_focus(focus: Optional[Dict[str, Any]], niche: Optional[str], followers: Optional[float]) -> bool:
    if not focus or niche != focus.get("niche"):
        return False
    return followers is None or focus["min_followers"] <= followers <= focus["max_followers"]


def rotate_focus(tasks: TaskStore, *, now: Optional[float] = None, rng: Any = None) -> Dict[str, Any]:
    """Keep one random focus active and its searches queued; pick a new one when it is used up."""
    import random
    import time as _time

    from ..niches import CITIES

    now = _time.time() if now is None else now
    rng = rng or random
    focus = current_focus(tasks)
    if focus:
        captured = tasks.connection.execute(
            """SELECT COUNT(*) FROM tasks WHERE kind='scrape.profile' AND state='done'
               AND json_extract(result, '$.eligible') = 1 AND json_extract(payload, '$.focus_id') = ?""",
            (focus["started_at"],),
        ).fetchone()[0]
        focus["captured"] = captured
        pending_focus_work = tasks.connection.execute(
            """SELECT COUNT(*) FROM tasks WHERE state IN ('queued','leased') AND priority >= ?
               AND kind IN ('scrape.profile','source.similar','source.search')""", (FOCUS_PRIORITY - 1,)
        ).fetchone()[0]
        # Barren = nothing found AND nothing left to try (e.g. "trekking in Patna"); queued work is not barren.
        barren = captured == 0 and pending_focus_work == 0 and now - focus["started_at"] > BARREN_SECONDS
        if now - focus["started_at"] > FOCUS_SECONDS or captured >= FOCUS_TARGET or barren:
            # The next focus goes first: leftovers of this one drop to the middle of the queue.
            tasks.connection.execute(
                "UPDATE tasks SET priority = 6 WHERE state = 'queued' AND priority >= ?", (FOCUS_PRIORITY - 1,))
            tasks.connection.commit()
            focus = None
    if focus is None:
        coverage = niche_coverage(tasks)
        niches = list(NICHES)
        # Random, but less-covered niches are likelier picks.
        seeded = {row[0] for row in tasks.connection.execute(
            "SELECT DISTINCT json_extract(result,'$.category') FROM tasks WHERE kind='scrape.profile' AND state IN ('done','skipped')")}
        # Less-covered niches are likelier; niches with nothing to expand from yet get half weight.
        weights = [(1.0 if niche in seeded else 0.5) / (1 + coverage.get(niche, 0)) for niche in niches]
        niche = rng.choices(niches, weights=weights, k=1)[0]
        low, high, label = rng.choice(SIZE_BANDS)
        focus = {"niche": niche, "city": rng.choice(CITIES), "min_followers": low, "max_followers": high,
                 "band": label, "started_at": now, "captured": 0}
    tasks.connection.execute(
        """INSERT INTO runtime_flags(name, value, updated_at) VALUES ('focus', ?, ?)
           ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (json.dumps(focus), now),
    )
    tasks.connection.commit()
    # Depth engine for the focus: similar accounts of creators we already have in this niche and
    # size band (30-80 leads per call), never expanded before, randomly chosen.
    focus_similars = tasks.connection.execute(
        "SELECT COUNT(*) FROM tasks WHERE kind='source.similar' AND state IN ('queued','leased') AND json_extract(payload,'$.focus') = 1"
    ).fetchone()[0]
    waiting = tasks.connection.execute(
        "SELECT COUNT(*) FROM tasks WHERE kind='scrape.profile' AND state='queued' AND json_extract(payload,'$.focus_id') = ?",
        (focus["started_at"],),
    ).fetchone()[0]
    seeded = focus.get("seeded", 0)
    # A focus gets a bounded number of seed expansions, and only while its own leads are being worked through.
    if focus_similars < 4 and seeded < FOCUS_SEEDS and waiting < FOCUS_WAITING_CAP:
        seed_sql = """SELECT t.key FROM tasks t
               WHERE t.kind='scrape.profile' AND t.state IN ('done','skipped')
                 AND json_extract(t.result,'$.category') = ?
                 AND COALESCE(t.last_error, '') NOT LIKE '%private%' AND COALESCE(t.last_error, '') NOT LIKE '%AI%'
                 AND COALESCE(t.last_error, '') NOT LIKE '%business%' AND COALESCE(t.last_error, '') NOT LIKE '%repost%'
                 AND COALESCE(t.last_error, '') NOT LIKE '%non-human%'
                 AND NOT EXISTS (SELECT 1 FROM tasks s WHERE s.kind='source.similar' AND s.key = t.key)
                 {size}
               ORDER BY RANDOM() LIMIT ?"""
        want = 4 - focus_similars
        size = "AND COALESCE(json_extract(t.result,'$.followers'), 0) BETWEEN ? AND ?"
        seeds = tasks.connection.execute(seed_sql.format(size=size), (focus["niche"], focus["min_followers"] / 2,
                                                                       focus["max_followers"] * 2, want)).fetchall()
        if not seeds:  # nobody of that size yet: any seed in the niche still leads to its neighbourhood
            seeds = tasks.connection.execute(seed_sql.format(size=""), (focus["niche"], want)).fetchall()
        for row in seeds[: FOCUS_SEEDS - seeded]:
            focus["seeded"] = focus.get("seeded", 0) + 1
            tasks.enqueue("source.similar", row["key"], {"username": row["key"], "focus": 1, "focus_id": focus["started_at"],
                                                         "niche": focus["niche"]}, priority=FOCUS_PRIORITY)
    tasks.connection.execute("UPDATE runtime_flags SET value=? WHERE name='focus'", (json.dumps(focus),))
    tasks.connection.commit()
    # Searches for the focus: its city first, then the niche elsewhere.
    terms = NICHES[focus["niche"]][1]
    queries = [f"{focus['city']} {term}" for term in terms] + queries_for(focus["niche"])
    queued = tasks.connection.execute(
        "SELECT COUNT(*) FROM tasks WHERE kind='source.search' AND state IN ('queued','leased') AND json_extract(payload,'$.focus') = 1"
    ).fetchone()[0]
    for query in queries:
        if queued >= 3:
            break
        if tasks.enqueue("source.search", query, {"query": query, "niche": focus["niche"], "focus": 1,
                                                  "focus_id": focus["started_at"]}, priority=FOCUS_PRIORITY):
            queued += 1
    return focus


def seed_searches(tasks: TaskStore) -> int:
    """Breadth first: every niche under its baseline keeps a couple of searches queued, least-covered first.

    Once all niches reach the baseline, searches keep flowing at low priority while
    similar-account expansion (depth) does most of the finding.
    """
    breadth_backlog = tasks.connection.execute(
        "SELECT COUNT(*) FROM tasks WHERE kind='scrape.profile' AND state='queued' AND priority BETWEEN 4 AND 9").fetchone()[0]
    if breadth_backlog > BREADTH_BACKLOG_CAP:
        return tasks.revisit_done("source.search", older_than_seconds=SEARCH_REVISIT_SECONDS)  # scraping catches up first
    coverage = niche_coverage(tasks)
    queued = {
        row["niche"]: row["n"] for row in tasks.connection.execute(
            """SELECT json_extract(payload, '$.niche') AS niche, COUNT(*) AS n FROM tasks
               WHERE kind='source.search' AND state IN ('queued', 'leased') GROUP BY niche""")
    }
    added = 0
    for niche in sorted(NICHES, key=lambda n: coverage.get(n, 0)):
        under = coverage.get(niche, 0) < BREADTH_TARGET
        want = SEARCHES_PER_NICHE if under else (1 if tasks.pending(["scrape.profile"]) < LOW_WATER else 0)
        have = queued.get(niche, 0)
        for query in queries_for(niche):
            if have >= want:
                break
            priority = breadth_priority(niche, coverage, wide=6, deep=1)  # background breadth, below the focus
            if tasks.enqueue("source.search", query, {"query": query, "niche": niche}, priority=priority):
                added += 1
                have += 1
    return added + tasks.revisit_done("source.search", older_than_seconds=SEARCH_REVISIT_SECONDS)
