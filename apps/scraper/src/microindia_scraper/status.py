"""Read-only project status: one snapshot for /status, the session hook and docs/STATE.md.

    python -m microindia_scraper.status            # short text
    python -m microindia_scraper.status --json     # full JSON
    python -m microindia_scraper.status --markdown docs/STATE.md

Never writes to the database (opened with mode=ro).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

DEFAULT_DATABASE = os.environ.get("MICROINDIA_DATABASE", "data/microindia.sqlite3")
GOVERNING_URL = "https://claude.ai/artifact/VhXp1pDab51CxYhpuyfLcZ"
STALL_MINUTES = 15


def _connect(database: str) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{os.path.abspath(database)}?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def _scalar(connection: sqlite3.Connection, sql: str, params: tuple = ()) -> Any:
    try:
        row = connection.execute(sql, params).fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return bool(_scalar(connection, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))


def _pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def snapshot(database: str = DEFAULT_DATABASE, run_dir: str = "run") -> Dict[str, Any]:
    now = time.time()
    connection = _connect(database)
    try:
        kinds: Dict[str, Dict[str, int]] = {}
        for row in connection.execute("SELECT kind, state, COUNT(*) AS n FROM tasks GROUP BY kind, state"):
            kinds.setdefault(row["kind"], {})[row["state"]] = row["n"]
        progress = {}
        for kind in kinds:
            last = _scalar(connection, "SELECT MAX(finished_at) FROM tasks WHERE kind=? AND state IN ('done','skipped')",
                           (kind,))
            hour = _scalar(connection, "SELECT COUNT(*) FROM tasks WHERE kind=? AND finished_at>?", (kind, now - 3600)) or 0
            progress[kind] = {
                "finished_last_hour": hour,
                "minutes_since_progress": round((now - last) / 60, 1) if last else None,
            }
        # Stalls are judged per runner, like the watchdog: on the work it may take right now
        # (its reported ``due`` honours backpressure) and on leases it holds past expiry.
        runner_health = {}
        runner_columns = {row[1] for row in connection.execute("PRAGMA table_info(runners)")}
        for row in connection.execute("SELECT * FROM runners WHERE status != 'stopped'"):
            own = [kind for kind in str(row["kinds"] or "").split(",") if kind]
            marks = ",".join("?" for _ in own) or "''"
            last = _scalar(connection, f"SELECT MAX(finished_at) FROM tasks WHERE kind IN ({marks}) "
                                       "AND state IN ('done','skipped')", tuple(own))
            expired = _scalar(connection, "SELECT COUNT(*) FROM tasks WHERE state='leased' AND leased_by=? "
                                          "AND lease_until<?", (row["runner_id"], now - 120)) or 0
            due = (row["due"] if "due" in runner_columns else None) or 0
            idle = round((now - last) / 60, 1) if last else None
            runner_health[row["runner_id"]] = {
                "due": due, "minutes_since_progress": idle, "expired_leases": expired,
                "stalled": expired > 0 or bool(due and (idle is None or idle > STALL_MINUTES)),
            }
        kept = _scalar(connection, "SELECT COUNT(*) FROM tasks WHERE kind='scrape.profile' AND state='done'") or 0
        kept_hour = _scalar(connection, "SELECT COUNT(*) FROM tasks WHERE kind='scrape.profile' AND state='done' "
                                        "AND finished_at>?", (now - 3600,)) or 0
        brand_ready = _scalar(connection, "SELECT COUNT(*) FROM tasks WHERE kind='scrape.profile' AND state='done' "
                                          "AND json_extract(result,'$.brand_ready')=1") or 0
        runners = [dict(row) for row in connection.execute(
            "SELECT runner_id, kinds, tabs, status, processed, last_error, last_heartbeat FROM runners")]
        for runner in runners:
            runner["heartbeat_age_s"] = round(now - runner.pop("last_heartbeat"), 1)
        insight = {}
        for table in ("reel_media", "reel_analyses", "creator_dossiers"):
            insight[table] = _scalar(connection, f"SELECT COUNT(*) FROM {table}") if _table_exists(connection, table) else None
        flags = {row["name"]: row["value"] for row in connection.execute("SELECT name, value FROM runtime_flags")}
        data = {
            "creators": _scalar(connection, "SELECT COUNT(*) FROM creators"),
            "posts": _scalar(connection, "SELECT COUNT(*) FROM posts"),
            "captures": _scalar(connection, "SELECT COUNT(*) FROM profile_captures"),
            "kept": kept,
            "kept_last_hour": kept_hour,
            "brand_ready": brand_ready,
            **insight,
        }
    finally:
        connection.close()

    workers: Dict[str, Any] = {}
    for plane, filename in (("collection", "local-supervisor-state.json"), ("insight", "insight-supervisor-state.json")):
        try:
            with open(os.path.join(run_dir, filename)) as handle:
                state = json.load(handle)
        except (OSError, ValueError):
            continue
        for name, info in (state.get("workers") or {}).items():
            workers[name] = {"plane": plane, "pid": info.get("pid"), "status": info.get("status"),
                             "alive": _pid_alive(info.get("pid"))}

    issues: List[str] = []
    if flags.get("network_down"):
        issues.append(f"network down, all runners paused: {flags['network_down'][:80]}")
    if flags.get("auth_blocked"):
        issues.append(f"Instagram sign-in needed: {flags['auth_blocked']}")
    for runner_id, info in runner_health.items():
        if info["stalled"] and not flags.get("network_down") and not flags.get("auth_blocked"):
            issues.append(f"runner {runner_id} stalled: {info['due']} tasks it may take, no progress for "
                          f"{info['minutes_since_progress']} min, {info['expired_leases']} expired leases")
    for name, info in workers.items():
        if info["status"] == "running" and not info["alive"]:
            issues.append(f"worker {name} marked running but pid {info['pid']} is dead")
    return {
        "checked_at": now,
        "governing_artifact": GOVERNING_URL,
        "data": data,
        "tasks": kinds,
        "progress": progress,
        "runner_health": runner_health,
        "runners": runners,
        "workers": workers,
        "focus": json.loads(flags["focus"]) if flags.get("focus") else None,
        "issues": issues,
    }


def short_text(snap: Dict[str, Any]) -> str:
    d = snap["data"]
    queued = sum(states.get("queued", 0) for states in snap["tasks"].values())
    lines = [
        f"microIndia · kept {d['kept']:,} creators (+{d['kept_last_hour']} last hour) · brand-ready {d['brand_ready']} "
        f"· queue {queued:,}",
    ]
    if d.get("reel_analyses") is not None:
        lines.append(f"insights · reels fetched {d.get('reel_media') or 0:,} · analysed {d.get('reel_analyses') or 0:,} "
                     f"· dossiers {d.get('creator_dossiers') or 0:,}")
    moving = ", ".join(f"{kind} {info['finished_last_hour']}/h" for kind, info in sorted(snap["progress"].items()))
    lines.append(f"throughput · {moving}")
    lines.append("issues · " + ("; ".join(snap["issues"]) if snap["issues"] else "none"))
    lines.append(f"governing artifact · {snap['governing_artifact']}")
    return "\n".join(lines)


def markdown(snap: Dict[str, Any]) -> str:
    d = snap["data"]
    when = time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(snap["checked_at"]))
    out = [
        "# microIndia state",
        "",
        f"Generated {when} by `python -m microindia_scraper.status --markdown docs/STATE.md`. Do not edit by hand.",
        f"Governing artifact: {snap['governing_artifact']}",
        "",
        "## Data",
        "",
        "| Measure | Value |",
        "|---|---|",
    ]
    for key, value in d.items():
        out.append(f"| {key.replace('_', ' ')} | {value if value is not None else '—'} |")
    out += ["", "## Pipeline", "", "| Kind | Queued | Done | Skipped | Failed | Last hour | Idle min |",
            "|---|---|---|---|---|---|---|"]
    for kind, states in sorted(snap["tasks"].items()):
        p = snap["progress"].get(kind, {})
        out.append(f"| `{kind}` | {states.get('queued', 0)} | {states.get('done', 0)} | {states.get('skipped', 0)} | "
                   f"{states.get('failed', 0)} | {p.get('finished_last_hour')} | {p.get('minutes_since_progress')} |")
    out += ["", "## Runners", "", "| Runner | May take now | Idle min | Expired leases | Stalled |", "|---|---|---|---|---|"]
    for runner_id, info in sorted(snap["runner_health"].items()):
        out.append(f"| {runner_id} | {info['due']} | {info['minutes_since_progress']} | {info['expired_leases']} | "
                   f"{'**yes**' if info['stalled'] else 'no'} |")
    out += ["", "## Workers", "", "| Worker | Plane | Status | Alive |", "|---|---|---|---|"]
    for name, info in sorted(snap["workers"].items()):
        out.append(f"| {name} | {info['plane']} | {info['status']} | {'yes' if info['alive'] else 'no'} |")
    out += ["", "## Open issues", ""]
    out += [f"- {issue}" for issue in snap["issues"]] or ["- none"]
    if snap.get("focus"):
        f = snap["focus"]
        out += ["", f"Current sourcing focus: {f.get('niche')} · {f.get('city')} · {f.get('band')}"]
    return "\n".join(out) + "\n"


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="read-only microIndia status")
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--run-dir", default="run")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--markdown", metavar="PATH")
    args = parser.parse_args(argv)
    snap = snapshot(args.database, args.run_dir)
    if args.markdown:
        with open(args.markdown, "w") as handle:
            handle.write(markdown(snap))
        print(f"wrote {args.markdown}")
    elif args.json:
        print(json.dumps(snap, indent=2, default=str))
    else:
        print(short_text(snap))


if __name__ == "__main__":
    main()
