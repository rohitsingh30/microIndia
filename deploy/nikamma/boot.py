"""microIndia on Nikamma: run the dashboard from code and data pushed to the pod's disk.

Runs on a stock python:3.12-slim image; nothing of microIndia is baked into any image.

    DATA_DIR/releases/<id>/src        code bundle (the microindia_scraper package)
    DATA_DIR/releases/<id>/dist       built dashboard
    DATA_DIR/current                  id of the release in use
    DATA_DIR/snapshots/<ts>.sqlite3   database snapshots; DATA_DIR/snapshot points at the one in use

Two servers:
  * APP_PORT (public): the dashboard API from the current release, read-only, against the current
    snapshot. Until both exist, a small "being set up" page answers instead.
  * DEPLOY_PORT (routed only for /__deploy): token-protected uploads.
      POST /__deploy/snapshot   body = sqlite file (gzip ok)  -> verified, then the app restarts on it
      POST /__deploy/code       body = tar.gz with src/ and dist/ -> verified, then the app restarts
      GET  /__deploy/status     what is running
    The token's SHA-256 is in DEPLOY_TOKEN_SHA256; the token itself never reaches the cluster.

Standard library only.
"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import io
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

DATA_DIR = Path(os.environ.get("DATA_DIR", "/srv/microindia"))
APP_PORT = int(os.environ.get("APP_PORT", "8080"))
DEPLOY_PORT = int(os.environ.get("DEPLOY_PORT", "8090"))
TOKEN_SHA256 = os.environ.get("DEPLOY_TOKEN_SHA256", "").strip().lower()
MAX_UPLOAD = int(os.environ.get("MAX_UPLOAD_MB", "150")) * 1_048_576
KEEP_RELEASES = 3
KEEP_SNAPSHOTS = 2

STATE = {"app": None, "started_at": None, "placeholder": None, "lock": threading.RLock()}


def log(**event) -> None:
    print(json.dumps({"ts": round(time.time(), 3), "component": "boot", **event}, sort_keys=True), flush=True)


# ---------------------------------------------------------------- layout


def _read(path: Path) -> Optional[str]:
    try:
        value = path.read_text().strip()
    except OSError:
        return None
    return value or None


def current_release() -> Optional[Path]:
    release = _read(DATA_DIR / "current")
    path = DATA_DIR / "releases" / release if release else None
    return path if path and (path / "src" / "microindia_scraper" / "__init__.py").exists() else None


def current_snapshot() -> Optional[Path]:
    name = _read(DATA_DIR / "snapshot")
    path = DATA_DIR / "snapshots" / name if name else None
    return path if path and path.exists() else None


def _write_pointer(name: str, value: str) -> None:
    tmp = DATA_DIR / f".{name}.tmp"
    tmp.write_text(value)
    os.replace(tmp, DATA_DIR / name)


def _prune(directory: Path, keep: int, in_use: Optional[str]) -> None:
    entries = sorted(directory.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    for entry in entries[keep:]:
        if entry.name == in_use:
            continue
        shutil.rmtree(entry, ignore_errors=True) if entry.is_dir() else entry.unlink(missing_ok=True)


# ---------------------------------------------------------------- the app process


class Placeholder(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = (b"<!doctype html><title>microIndia</title><meta name=viewport content='width=device-width'>"
                b"<p style='font:16px system-ui;margin:3rem'>microIndia is being set up. Data arrives shortly.</p>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


def _stop_app() -> None:
    process = STATE["app"]
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    STATE["app"] = None
    placeholder = STATE["placeholder"]
    if placeholder is not None:
        placeholder.shutdown()
        placeholder.server_close()
        STATE["placeholder"] = None


def start_app() -> None:
    """(Re)start whatever should serve APP_PORT right now."""
    with STATE["lock"]:
        _stop_app()
        release, snapshot = current_release(), current_snapshot()
        if release is None or snapshot is None:
            server = ThreadingHTTPServer(("0.0.0.0", APP_PORT), Placeholder)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            STATE["placeholder"] = server
            log(event="placeholder", release=bool(release), snapshot=bool(snapshot))
            return
        env = {**os.environ, "PYTHONPATH": str(release / "src"), "PYTHONUNBUFFERED": "1", "MICROINDIA_READ_ONLY": "1"}
        env.pop("DEPLOY_TOKEN_SHA256", None)
        STATE["app"] = subprocess.Popen(
            [sys.executable, "-m", "microindia_scraper.api", "--database", str(snapshot), "--host", "0.0.0.0",
             "--port", str(APP_PORT), "--dist", str(release / "dist"), "--health-file", "/tmp/health.json"],
            env=env, cwd="/tmp")
        STATE["started_at"] = time.time()
        log(event="app_started", release=release.name, snapshot=snapshot.name, pid=STATE["app"].pid)


def watch_app() -> None:
    """Restart the app if it dies (crash-only)."""
    while True:
        time.sleep(5)
        with STATE["lock"]:
            process = STATE["app"]
            if process is not None and process.poll() is not None:
                log(event="app_exited", code=process.returncode)
                time.sleep(2)
                start_app()


# ---------------------------------------------------------------- uploads


def _authorized(header: Optional[str]) -> bool:
    if not TOKEN_SHA256 or not header or not header.startswith("Bearer "):
        return False
    digest = hashlib.sha256(header[7:].strip().encode()).hexdigest()
    return hmac.compare_digest(digest, TOKEN_SHA256)


def receive_snapshot(body: bytes) -> dict:
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    if not body.startswith(b"SQLite format 3\x00"):
        raise ValueError("not a SQLite database")
    snapshots = DATA_DIR / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    name = time.strftime("%Y%m%dT%H%M%S") + f"-{hashlib.sha256(body).hexdigest()[:8]}.sqlite3"
    tmp = snapshots / f".{name}.part"
    tmp.write_bytes(body)
    check = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    try:
        ok = check.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        check.close()
    if not ok or "profile_captures" not in tables:
        tmp.unlink(missing_ok=True)
        raise ValueError("snapshot failed integrity check")
    os.replace(tmp, snapshots / name)
    _write_pointer("snapshot", name)
    start_app()
    _prune(snapshots, KEEP_SNAPSHOTS, name)
    return {"snapshot": name, "bytes": len(body)}


def receive_code(body: bytes) -> dict:
    release_id = hashlib.sha256(body).hexdigest()[:12]
    releases = DATA_DIR / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    staging = releases / f".{release_id}.part"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        archive.extractall(staging, filter="data")  # refuses absolute paths, "..", links outside, devices
    if not (staging / "src" / "microindia_scraper" / "__init__.py").exists() or not (staging / "dist" / "index.html").exists():
        shutil.rmtree(staging, ignore_errors=True)
        raise ValueError("bundle must contain src/microindia_scraper and dist/index.html")
    target = releases / release_id
    shutil.rmtree(target, ignore_errors=True)
    os.replace(staging, target)
    _write_pointer("current", release_id)
    start_app()
    _prune(releases, KEEP_RELEASES, release_id)
    return {"release": release_id}


class Deploy(BaseHTTPRequestHandler):
    def _answer(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/__deploy/status":
            return self._answer(404, {"error": "not found"})
        if not _authorized(self.headers.get("Authorization")):
            return self._answer(401, {"error": "unauthorized"})
        release, snapshot = current_release(), current_snapshot()
        process = STATE["app"]
        self._answer(200, {
            "release": release.name if release else None,
            "snapshot": snapshot.name if snapshot else None,
            "snapshot_bytes": snapshot.stat().st_size if snapshot else None,
            "app_running": process is not None and process.poll() is None,
            "app_started_at": STATE["started_at"],
        })

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in ("/__deploy/snapshot", "/__deploy/code"):
            return self._answer(404, {"error": "not found"})
        if not _authorized(self.headers.get("Authorization")):
            return self._answer(401, {"error": "unauthorized"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length <= 0 or length > MAX_UPLOAD:
            return self._answer(413, {"error": f"body must be 1 byte to {MAX_UPLOAD} bytes"})
        body = self.rfile.read(length)
        try:
            result = receive_snapshot(body) if self.path.endswith("snapshot") else receive_code(body)
        except Exception as exc:  # report, never crash the uploader
            log(event="upload_rejected", path=self.path, error=repr(exc))
            return self._answer(400, {"error": str(exc)})
        log(event="upload_accepted", path=self.path, **result)
        self._answer(200, result)

    def log_message(self, *_args) -> None:
        pass


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TOKEN_SHA256:
        log(event="no_deploy_token", note="uploads disabled until DEPLOY_TOKEN_SHA256 is set")
    start_app()
    threading.Thread(target=watch_app, daemon=True).start()
    deploy = ThreadingHTTPServer(("0.0.0.0", DEPLOY_PORT), Deploy)
    signal.signal(signal.SIGTERM, lambda *_: (_stop_app(), os._exit(0)))
    log(event="deploy_listening", port=DEPLOY_PORT)
    deploy.serve_forever()


if __name__ == "__main__":
    main()
