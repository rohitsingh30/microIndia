"""HTTP server: dispatches /api/* to the routes in this package and serves the built dashboard (127.0.0.1 only).

Every request runs on its own thread (ThreadingHTTPServer), so route code must stay quick: model calls
never run on a request thread.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

# Route modules register themselves on import. Later slices only edit their own module.
from . import ask, briefs, compare, creators, events, legacy_ai, local, media, ondemand, ops, shortlists  # noqa: F401
from . import router
from .ops import OpsQueries
from .repository import CreatorIndex

DEFAULT_DIST = Path(__file__).resolve().parents[4] / "dashboard" / "dist"
MAX_BODY = 1024 * 1024  # 1 MB


class Repository(CreatorIndex, OpsQueries):
    """Read model over the capture tables and the task table, plus the live-operations queries."""


class Handler(BaseHTTPRequestHandler):
    repo: Repository
    dist: Path
    server_version = "microindia-api/1"

    def log_message(self, format: str, *args: Any) -> None:  # quiet access log
        return

    def handle(self) -> None:
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError, TimeoutError):
            pass  # browser closed the stream (tab closed, reload); nothing to report

    def _send(self, status: int, body: bytes, content_type: str, extra: Optional[Dict[str, str]] = None) -> None:
        headers = {"Cache-Control": "no-store" if content_type.startswith("application/json") else "public, max-age=60"}
        headers.update(extra or {})
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(status, json.dumps(payload, default=str).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        self._timed("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._timed("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._timed("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._timed("DELETE")

    def _timed(self, method: str) -> None:
        started = time.time()
        try:
            self._dispatch(method)
        finally:
            elapsed = time.time() - started
            if elapsed > 2 and not self.path.startswith("/api/events"):
                print(json.dumps({"event": "slow_request", "method": method, "path": self.path[:120],
                                  "seconds": round(elapsed, 2), "threads": threading.active_count()}), flush=True)

    def _read_body(self) -> Any:
        """Parsed JSON body, {} when empty or not JSON. Raises HttpError(413) over MAX_BODY."""
        try:
            length = max(0, int(self.headers.get("Content-Length") or 0))
        except ValueError:
            length = 0
        if length > MAX_BODY:
            remaining = length
            while remaining > 0:  # drain so the client can read our answer instead of a reset
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
            raise router.HttpError(413, f"body over {MAX_BODY} bytes")
        raw = self.rfile.read(length) if length else b""
        try:
            return (json.loads(raw.decode()) or {}) if raw else {}
        except (UnicodeDecodeError, ValueError):
            return {}

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        multi = parse_qs(parsed.query)
        path = parsed.path
        try:
            body = None if method == "GET" else self._read_body()
            found = router.resolve(method, path)
            if found is None:
                if method == "GET" and not path.startswith("/api/"):
                    return self._static(path)
                return self._json({"error": "unknown action" if method == "POST" else "unknown endpoint"}, 404)
            fn, match = found
            request = router.Request(method=method, path=path, params={k: v[-1] for k, v in multi.items()},
                                     multi=multi, body=body, match=match, repo=self.repo)
            result = fn(request)
            if isinstance(result, router.Stream):
                return self._stream(result)
            if isinstance(result, router.Response):
                return self._send(result.status, result.body, result.content_type, result.headers)
            return self._json(result)
        except BrokenPipeError:
            return
        except router.HttpError as exc:
            return self._json({"error": exc.message}, exc.status)
        except Exception as exc:  # surface errors to the UI instead of hanging
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def _stream(self, stream: router.Stream) -> None:
        self.send_response(200)
        self.send_header("Content-Type", stream.content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        def write(data: bytes) -> None:
            self.wfile.write(data)
            self.wfile.flush()

        stream.run(write)

    def _static(self, path: str) -> None:
        root = self.dist.resolve()
        target = (root / path.lstrip("/")).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            target = root / "index.html"
        if not target.is_file():
            body = b"<h1>Dashboard not built</h1><p>Run <code>npm run build</code> in apps/dashboard.</p>"
            return self._send(200, body, "text/html; charset=utf-8")
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        extra = {"Cache-Control": "no-cache"} if target.name == "index.html" else None
        self._send(200, target.read_bytes(), content_type, extra)


def make_server(database: str, host: str, port: int, dist: Path, health_file: str) -> ThreadingHTTPServer:
    """Build the repository, run startup hooks, and bind (port 0 picks a free port)."""
    repo = Repository(database, health_file)
    for hook in router.startup_hooks():
        hook(repo)
    handler = type("BoundHandler", (Handler,), {"repo": repo, "dist": Path(dist)})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(database: str, host: str, port: int, dist: Path, health_file: str) -> None:
    server = make_server(database, host, port, dist, health_file)
    print(json.dumps({"event": "api_listening", "url": f"http://{host}:{server.server_port}", "dist": str(dist)}), flush=True)
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="microIndia dashboard API")
    parser.add_argument("--database", default=os.environ.get("MICROINDIA_DATABASE", "data/microindia.sqlite3"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--dist", default=str(DEFAULT_DIST))
    parser.add_argument("--health-file", default="run/health.json")
    args = parser.parse_args()
    serve(args.database, args.host, args.port, Path(args.dist), args.health_file)
