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

@dataclass
class RunnerProgress:
    """What one runner can and did do, judged only on its own task kinds."""

    due: int  # tasks it may take right now, as the runner itself reports (honours backpressure)
    last_finished_at: Optional[float]  # newest done/skipped task of its kinds
    expired_leases: int  # tasks it still holds past their lease (+ grace): its slots are stuck


@dataclass
class Observation:
    now: float
    cdp_ok: bool
    heartbeats: Dict[str, float]  # runner id -> last heartbeat
    last_finished_at: Optional[float]
    queued: int
    auth_blocked: Optional[str]
    progress: Dict[str, RunnerProgress] = field(default_factory=dict)
    network_down: Optional[str] = None


@dataclass
class Policy:
    cdp_failures_before_restart: int = 3
    stale_heartbeat_seconds: float = 300.0
    stuck_queue_seconds: float = 900.0
    grace_seconds: float = 180.0
    # Longer than the runner's own stuck check (120s) + housekeeping + hard exit (60s), so a runner
    # gets to exit by itself before the watchdog restarts it.
    lease_grace_seconds: float = 300.0


@dataclass
class WatchdogState:
    cdp_failures: int = 0
    last_restart: Dict[str, float] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)


def decide(obs: Observation, state: WatchdogState, policy: Policy = Policy()) -> List[Dict[str, str]]:
    """Pure decision step: which workers to restart and why.

    Each runner is judged on its own kinds, so a busy sourcer can no longer hide a dead scraper.
    """
    actions: List[Dict[str, str]] = []

    def cooling(worker: str) -> bool:
        since = obs.now - state.last_restart.get(worker, state.started_at - policy.grace_seconds)
        return since < policy.grace_seconds

    def acting(worker: str) -> bool:
        return any(action["worker"] == worker for action in actions)

    state.cdp_failures = 0 if obs.cdp_ok else state.cdp_failures + 1
    if state.cdp_failures >= policy.cdp_failures_before_restart and not cooling("chrome"):
        actions.append({"worker": "chrome", "reason": f"CDP unreachable {state.cdp_failures} checks in a row"})
        state.cdp_failures = 0
    for worker, beat in obs.heartbeats.items():
        if beat is not None and obs.now - beat > policy.stale_heartbeat_seconds and not cooling(worker):
            actions.append({"worker": worker, "reason": f"no heartbeat for {obs.now - beat:.0f}s"})
    paused = obs.auth_blocked or obs.network_down or not obs.cdp_ok
    for worker, progress in sorted(obs.progress.items()):
        if paused or acting(worker) or cooling(worker):
            continue
        if progress.expired_leases:
            actions.append({"worker": worker,
                            "reason": f"{progress.expired_leases} task(s) held past their lease: slots stuck"})
            continue
        stalled_for = obs.now - (progress.last_finished_at or state.started_at)
        if progress.due > 0 and stalled_for > policy.stuck_queue_seconds:
            actions.append({"worker": worker,
                            "reason": f"{progress.due} tasks due but none of its kinds finished for {stalled_for:.0f}s"})
    for action in actions:
        state.last_restart[action["worker"]] = obs.now
    return actions


def observe(database: str, cdp_url: str, *, lease_grace_seconds: float = Policy.lease_grace_seconds) -> Observation:
    try:
        urllib.request.urlopen(f"{cdp_url}/json/version", timeout=5).read()
        cdp_ok = True
    except Exception:
        cdp_ok = False
    now = time.time()
    connection = sqlite3.connect(database, timeout=10)
    connection.row_factory = sqlite3.Row
    heartbeats: Dict[str, float] = {}
    progress: Dict[str, RunnerProgress] = {}
    last, queued, flag, network = None, 0, None, None
    try:
        try:
            runners = connection.execute("SELECT * FROM runners WHERE status != 'stopped'").fetchall()
            for row in runners:
                heartbeats[row["runner_id"]] = row["last_heartbeat"]
                kinds = [kind for kind in str(row["kinds"] or "").split(",") if kind]
                if not kinds:
                    continue
                marks = ",".join("?" for _ in kinds)
                finished = connection.execute(
                    f"SELECT MAX(finished_at) FROM tasks WHERE kind IN ({marks}) AND state IN ('done','skipped')",
                    kinds).fetchone()[0]
                expired = connection.execute(
                    "SELECT COUNT(*) FROM tasks WHERE state='leased' AND leased_by=? AND lease_until < ?",
                    (row["runner_id"], now - lease_grace_seconds)).fetchone()[0]
                due = row["due"] if "due" in row.keys() and row["due"] is not None else 0
                progress[row["runner_id"]] = RunnerProgress(int(due), finished, int(expired))
            last = connection.execute("SELECT MAX(finished_at) FROM tasks").fetchone()[0]
            # Only work that is due counts; retries waiting on run_at are not a stall.
            queued = connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE state='queued' AND run_at <= ?", (now,)
            ).fetchone()[0]
            flags = {r["name"]: r["value"] for r in connection.execute(
                "SELECT name, value FROM runtime_flags WHERE name IN ('auth_blocked', 'network_down')")}
            flag, network = flags.get("auth_blocked"), flags.get("network_down")
        except sqlite3.OperationalError:
            pass
    finally:
        connection.close()
    return Observation(now, cdp_ok, heartbeats, last, int(queued), flag, progress, network)


def restart_worker(state_paths: Any, worker: str) -> Optional[int]:
    """Signal the worker's pid; whichever supervisor owns it restarts it.

    ``state_paths`` is one supervisor state file or a list of them (collection and insight planes).
    """
    for state_path in [state_paths] if isinstance(state_paths, str) else list(state_paths):
        try:
            workers = json.load(open(state_path)).get("workers", {})
        except (OSError, json.JSONDecodeError):
            continue
        info = workers.get(worker) or {}
        pid = info.get("pid")
        if not pid or info.get("status") != "running":
            continue
        try:
            os.kill(int(pid), signal.SIGTERM)
        except ProcessLookupError:
            continue
        return int(pid)
    return None


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
        "network_down": obs.network_down,
        "progress": {name: {"due": p.due, "last_finished_at": p.last_finished_at, "expired_leases": p.expired_leases}
                     for name, p in obs.progress.items()},
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
    parser.add_argument("--supervisor-state", action="append", default=None,
                        help="supervisor state file(s); default: collection and insight planes")
    parser.add_argument("--health-file", default="run/health.json")
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--sweep-every", type=int, default=4, help="hung-tab sweep every N cycles")
    args = parser.parse_args()
    args.supervisor_state = args.supervisor_state or ["run/local-supervisor-state.json",
                                                      "run/insight-supervisor-state.json"]
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
