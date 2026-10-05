"""Self-healing watchdog for the always-on collector.

Every cycle it checks Chrome's CDP endpoint, runner heartbeats and queue
progress, and restarts whatever is stuck by signalling the worker's pid; the
supervisor (restart: true) brings it back. It also writes ``run/health.json``
for the dashboard.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import signal
import sqlite3
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

RUNNER_WORKERS = ("sourcer", "scraper")


@dataclass
class Observation:
    now: float
    cdp_ok: bool
    heartbeats: Dict[str, float]  # runner id -> last heartbeat
    last_finished_at: Optional[float]
    queued: int
    auth_blocked: Optional[str]


@dataclass
class Policy:
    cdp_failures_before_restart: int = 3
    stale_heartbeat_seconds: float = 300.0
    stuck_queue_seconds: float = 900.0
    grace_seconds: float = 180.0


@dataclass
class WatchdogState:
    cdp_failures: int = 0
    last_restart: Dict[str, float] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)


def decide(obs: Observation, state: WatchdogState, policy: Policy = Policy()) -> List[Dict[str, str]]:
    """Pure decision step: which workers to restart and why."""
    actions: List[Dict[str, str]] = []

    def cooling(worker: str) -> bool:
        since = obs.now - state.last_restart.get(worker, state.started_at - policy.grace_seconds)
        return since < policy.grace_seconds

    state.cdp_failures = 0 if obs.cdp_ok else state.cdp_failures + 1
    if state.cdp_failures >= policy.cdp_failures_before_restart and not cooling("chrome"):
        actions.append({"worker": "chrome", "reason": f"CDP unreachable {state.cdp_failures} checks in a row"})
        state.cdp_failures = 0
    for worker in RUNNER_WORKERS:
        beat = obs.heartbeats.get(worker)
        if beat is not None and obs.now - beat > policy.stale_heartbeat_seconds and not cooling(worker):
            actions.append({"worker": worker, "reason": f"no heartbeat for {obs.now - beat:.0f}s"})
    stalled_for = obs.now - (obs.last_finished_at or state.started_at)
    if (
        obs.queued > 0
        and not obs.auth_blocked
        and obs.cdp_ok
        and stalled_for > policy.stuck_queue_seconds
        and not any(action["worker"] in RUNNER_WORKERS for action in actions)
    ):
        for worker in RUNNER_WORKERS:
            if not cooling(worker):
                actions.append({"worker": worker, "reason": f"{obs.queued} tasks queued but none finished for {stalled_for:.0f}s"})
    for action in actions:
        state.last_restart[action["worker"]] = obs.now
    return actions


def observe(database: str, cdp_url: str) -> Observation:
    try:
        urllib.request.urlopen(f"{cdp_url}/json/version", timeout=5).read()
        cdp_ok = True
    except Exception:
        cdp_ok = False
    connection = sqlite3.connect(database, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        try:
            heartbeats = {row["runner_id"]: row["last_heartbeat"] for row in connection.execute(
                "SELECT runner_id, last_heartbeat FROM runners WHERE status != 'stopped'")}
            last = connection.execute("SELECT MAX(finished_at) FROM tasks").fetchone()[0]
            # Only work that is due counts; retries waiting on run_at are not a stall.
            queued = connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE state='queued' AND run_at <= ?", (time.time(),)
            ).fetchone()[0]
            flag = connection.execute("SELECT value FROM runtime_flags WHERE name='auth_blocked'").fetchone()
        except sqlite3.OperationalError:
            heartbeats, last, queued, flag = {}, None, 0, None
    finally:
        connection.close()
    return Observation(time.time(), cdp_ok, heartbeats, last, int(queued), flag[0] if flag else None)


def restart_worker(state_path: str, worker: str) -> Optional[int]:
    """Signal the worker's pid; the supervisor restarts it."""
    try:
        workers = json.load(open(state_path)).get("workers", {})
    except (OSError, json.JSONDecodeError):
        return None
    info = workers.get(worker) or {}
    pid = info.get("pid")
    if not pid or info.get("status") != "running":
        return None
    try:
        os.kill(int(pid), signal.SIGTERM)
    except ProcessLookupError:
        return None
    return int(pid)


def sample_system(cdp_url: str, database: str) -> Dict[str, float]:
    """Memory/CPU per component (from `ps`), swap, free memory, open tabs, database size."""
    import subprocess

    groups = {"chrome": 0.0, "chrome_cpu": 0.0, "runners": 0.0, "runners_cpu": 0.0, "api": 0.0, "api_cpu": 0.0}
    try:
        listing = subprocess.run(["/bin/ps", "-axo", "rss=,%cpu=,command="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        listing = ""
    for line in listing.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        rss_mb, cpu, command = float(parts[0]) / 1024, float(parts[1]), parts[2]
        if "Chrome-cdp-profile" in command or ("Google Chrome" in command and "remote-debugging-port" in command):
            key = "chrome"
        elif "microindia_scraper.run" in command or "playwright" in command and "driver" in command:
            key = "runners"
        elif "microindia_scraper.api" in command:
            key = "api"
        else:
            continue
        groups[key] += rss_mb
        groups[f"{key}_cpu"] += cpu
    metrics = {f"{name}_mb" if not name.endswith("_cpu") else name: round(value, 1) for name, value in groups.items()}
    try:
        swap = subprocess.run(["/usr/sbin/sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, timeout=5).stdout
        used = re.search(r"used = ([\d.]+)M", swap)
        metrics["swap_used_mb"] = float(used.group(1)) if used else 0.0
        pressure = subprocess.run(["/usr/bin/memory_pressure"], capture_output=True, text=True, timeout=10).stdout
        free = re.search(r"free percentage: (\d+)%", pressure)
        metrics["memory_free_pct"] = float(free.group(1)) if free else 0.0
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        tabs = json.load(urllib.request.urlopen(f"{cdp_url}/json/list", timeout=5))
        metrics["chrome_tabs"] = float(sum(1 for t in tabs if t.get("type") == "page"))
    except Exception:
        pass
    try:
        metrics["db_mb"] = round(sum(os.path.getsize(p) for p in (database, database + "-wal") if os.path.exists(p)) / 1_048_576, 1)
    except OSError:
        pass
    return metrics


def record_metrics(database: str, metrics: Dict[str, float], now: float, keep_days: float = 7.0) -> None:
    connection = sqlite3.connect(database, timeout=10)
    try:
        connection.executemany("INSERT OR REPLACE INTO metrics(ts, name, value) VALUES (?, ?, ?)",
                               [(now, name, value) for name, value in metrics.items()])
        connection.execute("DELETE FROM metrics WHERE ts < ?", (now - keep_days * 86400,))
        connection.commit()
    except sqlite3.OperationalError:
        pass  # tables not created yet (runtime not started)
    finally:
        connection.close()


def write_health(path: str, obs: Observation, state: WatchdogState, recent: List[Dict[str, Any]]) -> None:
    payload = {
        "checked_at": obs.now,
        "chrome": {"ok": obs.cdp_ok, "consecutive_failures": state.cdp_failures},
        "runners": {name: {"last_heartbeat": beat, "age_seconds": round(obs.now - beat, 1)}
                    for name, beat in obs.heartbeats.items()},
        "last_task_finished_at": obs.last_finished_at,
        "queued_due": obs.queued,
        "auth_blocked": obs.auth_blocked,
        "recent_actions": recent[-20:],
    }
    tmp = f"{path}.tmp"
    with open(tmp, "w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    os.replace(tmp, path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Keep the microIndia collector healthy")
    parser.add_argument("--database", default=os.environ.get("MICROINDIA_DATABASE", "data/microindia.sqlite3"))
    parser.add_argument("--cdp-url", default=os.environ.get("MICROINDIA_CDP_URL", "http://127.0.0.1:9222"))
    parser.add_argument("--supervisor-state", default="run/local-supervisor-state.json")
    parser.add_argument("--health-file", default="run/health.json")
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--sweep-every", type=int, default=4, help="hung-tab sweep every N cycles")
    args = parser.parse_args()
    state = WatchdogState()
    recent: List[Dict[str, Any]] = []
    cycle = 0
    while True:
        cycle += 1
        obs = observe(args.database, args.cdp_url)
        for action in decide(obs, state):
            pid = restart_worker(args.supervisor_state, action["worker"])
            event = {"ts": obs.now, "action": "restart", "pid": pid, **action}
            recent.append(event)
            print(json.dumps({"event": "watchdog_restart", **event}), flush=True)
        if obs.cdp_ok and cycle % args.sweep_every == 0:
            try:
                from .runtime.browser import close_unresponsive_tabs

                owned = set()
                for ledger in glob.glob(os.path.join(os.path.dirname(args.health_file), "tabs", "tabs-*.json")):
                    try:
                        owned |= set(json.load(open(ledger)))
                    except (OSError, ValueError):
                        continue
                hung = close_unresponsive_tabs(args.cdp_url, skip=owned)
                if hung:
                    event = {"ts": obs.now, "action": "closed_hung_tabs", "targets": hung}
                    recent.append(event)
                    print(json.dumps({"event": "watchdog_closed_tabs", **event}), flush=True)
            except Exception as exc:
                print(json.dumps({"event": "watchdog_sweep_error", "error": repr(exc)}), flush=True)
        write_health(args.health_file, obs, state, recent)
        try:
            record_metrics(args.database, sample_system(args.cdp_url, args.database), obs.now)
        except Exception as exc:
            print(json.dumps({"event": "metrics_error", "error": repr(exc)}), flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
