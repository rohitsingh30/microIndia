"""Small local process supervisor for long-running microIndia workers.

The supervisor deliberately manages explicit commands from a JSON config instead
of importing worker modules. This keeps it useful for the browser dispatcher,
the dashboard, and browser-independent backfill/analysis workers without sharing
event loops or Chrome sessions.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


DEFAULT_CONFIG = "run/local-workers.json"
DEFAULT_STATE = "run/local-supervisor-state.json"

@dataclass
class WorkerSpec:
    name: str
    command: List[str]
    log_file: str
    restart: bool = True
    max_restarts: Optional[int] = 10
    cooldown_seconds: float = 5.0


def load_specs(path: str) -> List[WorkerSpec]:
    payload = json.loads(Path(path).read_text())
    specs = []
    for item in payload.get("workers", []):
        name = str(item["name"])
        command = [os.path.expandvars(str(value)) for value in item["command"]]
        if not command:
            raise ValueError(f"Worker {name!r} has an empty command")
        raw_max_restarts = item.get("max_restarts", 10)
        max_restarts = None if raw_max_restarts is None else int(raw_max_restarts)
        specs.append(WorkerSpec(
            name=name,
            command=command,
            log_file=os.path.expandvars(str(item.get("log_file", f"run/{name}.log"))),
            restart=bool(item.get("restart", True)),
            max_restarts=max_restarts,
            cooldown_seconds=float(item.get("cooldown_seconds", 5.0)),
        ))
    names = [spec.name for spec in specs]
    if len(names) != len(set(names)):
        raise ValueError("Worker names must be unique")
    return specs



def _write_state(path: str, state: Dict[str, object]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True))
    temporary.replace(target)


def _read_state(path: str) -> Dict[str, object]:
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"supervisor_pid": None, "workers": {}}


def status(state_path: str) -> Dict[str, object]:
    state = _read_state(state_path)
    workers = state.get("workers", {})
    if isinstance(workers, dict):
        for worker in workers.values():
            if isinstance(worker, dict) and worker.get("pid"):
                try:
                    os.kill(int(worker["pid"]), 0)
                    worker["alive"] = True
                except OSError:
                    worker["alive"] = False
    return state


class Supervisor:
    def __init__(self, specs: List[WorkerSpec], state_path: str) -> None:
        self.specs = specs
        self.state_path = state_path
        self.processes: Dict[str, Optional[subprocess.Popen]] = {}
        self.restart_counts = {spec.name: 0 for spec in specs}
        self.next_start_at = {spec.name: 0.0 for spec in specs}
        self.stopping = False

    def stop(self, *_: object) -> None:
        self.stopping = True
        for process in self.processes.values():
            if process is not None and process.poll() is None:
                process.terminate()

    def _start(self, spec: WorkerSpec) -> None:
        log_path = Path(spec.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", buffering=1) as log:
            process = subprocess.Popen(
                spec.command,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        self.processes[spec.name] = process
        self._save(spec.name, {
            "pid": process.pid,
            "command": spec.command,
            "status": "running",
            "return_code": None,
            "started_at": time.time(),
            "exited_at": None,
        })

    def _save(self, name: str, update: Dict[str, object]) -> None:
        state = _read_state(self.state_path)
        state["supervisor_pid"] = os.getpid()
        workers = state.setdefault("workers", {})
        if isinstance(workers, dict):
            current = workers.setdefault(name, {})
            if isinstance(current, dict):
                current.update(update)
                current["restart_count"] = self.restart_counts.get(name, 0)
        _write_state(self.state_path, state)

    def _restart_allowed(self, spec: WorkerSpec) -> bool:
        return spec.restart and (
            spec.max_restarts is None or self.restart_counts[spec.name] < spec.max_restarts
        )

    def run(self, poll_seconds: float = 2.0) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        state = _read_state(self.state_path)
        workers = state.get("workers")
        if isinstance(workers, dict):
            active_names = {spec.name for spec in self.specs}
            for name in list(workers):
                if name not in active_names:
                    del workers[name]
        _write_state(self.state_path, state)
        for spec in self.specs:
            self._start(spec)
        while not self.stopping:
            now = time.monotonic()
            for spec in self.specs:
                process = self.processes.get(spec.name)
                if process is not None and process.poll() is None:
                    continue
                if process is not None:
                    return_code = process.returncode
                    self._save(spec.name, {
                        "pid": process.pid,
                        "status": "exited",
                        "return_code": return_code,
                        "exited_at": time.time(),
                    })
                    self.processes[spec.name] = None
                    if not self._restart_allowed(spec):
                        continue
                    self.restart_counts[spec.name] += 1
                    self.next_start_at[spec.name] = now + spec.cooldown_seconds
                if self._restart_allowed(spec) and now >= self.next_start_at[spec.name]:
                    if not self.stopping:
                        self._start(spec)
            time.sleep(max(0.1, poll_seconds))
        for process in self.processes.values():
            if process is None:
                continue
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        state = _read_state(self.state_path)
        state["supervisor_pid"] = None
        state["stopped_at"] = time.time()
        _write_state(self.state_path, state)


def _acquire_lock(path: str) -> object:
    import fcntl

    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open("w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError(f"Another supervisor already owns {lock_path}")
    return lock


def main() -> None:
    parser = argparse.ArgumentParser(description="Supervise local microIndia worker processes")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--lock", default="run/local-supervisor.lock")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if args.status:
        print(json.dumps(status(args.state), indent=2, sort_keys=True))
        return
    lock = _acquire_lock(args.lock)
    try:
        specs = load_specs(args.config)
        Supervisor(specs, args.state).run(args.poll_seconds)
    finally:
        lock.close()


if __name__ == "__main__":
    main()