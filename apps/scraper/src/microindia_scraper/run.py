"""Run the sourcer, the scraper, or both, on the signed-in Chrome.

    python -m microindia_scraper.run work --kinds 'source.*' --tabs 2
    python -m microindia_scraper.run work --kinds 'scrape.*' --tabs 6
    python -m microindia_scraper.run work --kinds '*' --tabs 8
    python -m microindia_scraper.run add source.list data/seeds.txt
    python -m microindia_scraper.run status
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sqlite3
import sys
from typing import List

from . import handlers
from .constants import COHORT_MAX_FOLLOWERS, COHORT_MIN_FOLLOWERS
from .runtime import TaskStore, handlers_for, registered_kinds
from .runtime.browser import BrowserSession
from .runtime.pacing import Pacer
from .runtime.runner import Runner, log

DEFAULT_DATABASE = os.environ.get("MICROINDIA_DATABASE", "data/microindia.sqlite3")
DEFAULT_CDP_URL = os.environ.get("MICROINDIA_CDP_URL", "http://127.0.0.1:9222")


def _schedulers_for(kinds: List[str]) -> list:
    selected = []
    if "source.search" in kinds:
        selected.append(handlers.seed_searches)
        selected.append(handlers.sourcing.rotate_focus)
    if "scrape.profile" in kinds:
        selected.append(handlers.refresh_eligible)
    return selected


def _backpressure(limit: int):
    """Hold sourcing while plenty of profiles already wait to be scraped: no point finding more yet."""
    # Under a big backlog, only urgent similar-account expansion (the current focus) runs;
    # breadth searches are never held back.
    def gate(tasks: TaskStore) -> dict:
        if limit <= 0:
            return {}
        pending = tasks.pending(["scrape.profile"])
        if pending > limit * 5:
            return {"source.similar": 10}  # only the few seed expansions that start each focus
        if pending > limit:
            return {"source.similar": 9}   # focus-level depth only
        return {}

    return gate


def work(args: argparse.Namespace) -> None:
    kinds = handlers_for(args.kinds.split(","))
    if not kinds:
        raise SystemExit(f"--kinds {args.kinds!r} matches none of: {', '.join(registered_kinds())}")
    tasks = TaskStore(args.database, max_attempts=args.max_attempts)
    runner = Runner(
        tasks=tasks,
        session=BrowserSession(args.cdp_url, pacer=Pacer(args.database)),
        kinds=kinds,
        tabs=args.tabs,
        database=args.database,
        runner_id=args.name or f"{socket.gethostname()}:{args.kinds}",
        poll_seconds=args.poll_seconds,
        schedulers=_schedulers_for(kinds),
        max_tasks=args.max_tasks,
        gate=_backpressure(args.backlog_limit),
    )
    try:
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        log(event="runner_exit", error=repr(exc))
        raise SystemExit(1)
    finally:
        tasks.close()


def add(args: argparse.Namespace) -> None:
    tasks = TaskStore(args.database)
    payload = json.loads(args.payload) if args.payload else {}
    field = {"source.search": "query", "source.list": "path"}.get(args.kind, "username")
    if args.kind == "source.list":
        args.key = os.path.abspath(args.key)
    payload.setdefault(field, args.key)
    added = tasks.enqueue(args.kind, args.key, payload, priority=args.priority, revisit=True)
    tasks.close()
    print(json.dumps({"kind": args.kind, "key": args.key, "queued": added}))


def status(args: argparse.Namespace) -> None:
    tasks = TaskStore(args.database)
    runners = [dict(row) for row in tasks.connection.execute("SELECT * FROM runners ORDER BY runner_id")]
    print(json.dumps({"auth_blocked": tasks.auth_blocked(), "tasks": tasks.counts(), "runners": runners},
                     indent=2, sort_keys=True))
    tasks.close()


def unblock(args: argparse.Namespace) -> None:
    tasks = TaskStore(args.database)
    print(json.dumps({"requeued": tasks.clear_auth_blocked()}))
    tasks.close()


def migrate(args: argparse.Namespace) -> None:
    """Carry existing captures and queued jobs into the task table (safe to rerun)."""
    tasks = TaskStore(args.database)
    connection = tasks.connection
    counts = {"captured": 0, "eligible": 0, "queued": 0}
    try:
        rows = connection.execute(
            """SELECT c.profile_url, c.status, c.capture_id, MAX(c.captured_at) AS captured_at, p.payload AS profile
               FROM profile_captures c LEFT JOIN profile_snapshots p ON p.capture_id = c.capture_id
               GROUP BY c.profile_url"""
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    for row in rows:
        username = handlers.common.username_of(row["profile_url"])
        if not username:
            continue
        followers = (json.loads(row["profile"] or "{}") or {}).get("follower_count")
        eligible = row["status"] in handlers.scraping.ELIGIBLE_STATUSES and isinstance(followers, (int, float)) \
            and COHORT_MIN_FOLLOWERS <= followers <= COHORT_MAX_FOLLOWERS
        state = "done" if eligible else "skipped"
        result = json.dumps({"capture_id": row["capture_id"], "status": row["status"], "eligible": eligible,
                             "migrated": True}, sort_keys=True)
        cursor = connection.execute(
            """INSERT INTO tasks(kind, key, payload, state, run_at, result, created_at, updated_at, finished_at)
               VALUES ('scrape.profile', ?, ?, ?, strftime('%s','now'), ?, strftime('%s','now'),
                       strftime('%s','now'), strftime('%s','now'))
               ON CONFLICT(kind, key) DO NOTHING""",
            (username, json.dumps({"username": username}), state, result),
        )
        counts["captured"] += cursor.rowcount
        if eligible:
            counts["eligible"] += tasks.enqueue("source.similar", username, {"username": username}, priority=4)
    try:
        jobs = connection.execute(
            "SELECT payload FROM collection_jobs WHERE status IN ('queued', 'leased')"
        ).fetchall()
    except sqlite3.OperationalError:
        jobs = []
    for job in jobs:
        username = handlers.common.username_of(json.loads(job["payload"]).get("profile_url") or "")
        if username:
            counts["queued"] += tasks.enqueue("scrape.profile", username, {"username": username}, priority=2)
    connection.commit()
    tasks.close()
    print(json.dumps(counts))


def main(argv: List[str] = None) -> None:
    parser = argparse.ArgumentParser(description="microIndia browser-task runtime")
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    sub = parser.add_subparsers(dest="command", required=True)

    p_work = sub.add_parser("work", help="lease and run tasks on the signed-in Chrome")
    p_work.add_argument("--kinds", default="*", help="comma-separated globs, e.g. 'source.*' or 'scrape.*'")
    p_work.add_argument("--tabs", type=int, default=4)
    p_work.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    p_work.add_argument("--name", default=None, help="stable runner id; leases are reclaimed on restart")
    p_work.add_argument("--poll-seconds", type=float, default=2.0)
    p_work.add_argument("--max-tasks", type=int, default=0, help="stop after N tasks (0 = run forever)")
    p_work.add_argument("--max-attempts", type=int, default=3)
    p_work.add_argument("--backlog-limit", type=int, default=3000,
                        help="pause search/similar sourcing while more scrape tasks than this are pending (0 = never)")
    p_work.set_defaults(func=work)

    p_add = sub.add_parser("add", help="queue one task, e.g. add source.list seeds.txt")
    p_add.add_argument("kind", choices=registered_kinds())
    p_add.add_argument("key")
    p_add.add_argument("--payload", default="")
    p_add.add_argument("--priority", type=int, default=5)
    p_add.set_defaults(func=add)

    sub.add_parser("status", help="task counts by kind/state, runners, auth pause").set_defaults(func=status)
    sub.add_parser("unblock", help="clear the auth pause after signing in again").set_defaults(func=unblock)
    sub.add_parser("migrate", help="import existing captures and queued jobs").set_defaults(func=migrate)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
