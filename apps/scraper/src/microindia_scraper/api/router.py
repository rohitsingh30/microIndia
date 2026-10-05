"""Tiny router for the API: route modules register handlers here; server.py dispatches to them.

A route function takes a Request and returns a plain value (sent as JSON 200), a Response from
json()/csv()/file(), or a Stream (Server-Sent Events). Raise HttpError(status, msg) for a JSON error.

    @route("GET", r"/api/things/(?P<thing>[^/]+)")
    def thing(request: Request) -> Any:
        return request.repo.thing(request.match["thing"])

Patterns are full-match regexes over the raw URL path. Routes are tried in registration order.
"""

from __future__ import annotations

import json as _jsonlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Pattern, Tuple

RouteFn = Callable[["Request"], Any]

_ROUTES: List[Tuple[str, Pattern[str], RouteFn]] = []
_STARTUP: List[Callable[[Any], None]] = []


@dataclass
class Request:
    method: str
    path: str
    params: Dict[str, str]  # query string, last value wins
    multi: Dict[str, List[str]]  # query string, every value
    body: Any  # parsed JSON body ({} when empty or not JSON); None for GET
    match: "re.Match[str]"
    repo: Any


@dataclass
class Response:
    status: int
    body: bytes
    content_type: str
    headers: Dict[str, str] = field(default_factory=dict)


@dataclass
class Stream:
    """A long-lived response: `run(write)` is called after the headers; write(bytes) sends and flushes."""

    run: Callable[[Callable[[bytes], None]], None]
    content_type: str = "text/event-stream"


class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def json(payload: Any, status: int = 200) -> Response:
    return Response(status, _jsonlib.dumps(payload, default=str).encode(), "application/json; charset=utf-8")


def csv(text: str, filename: str) -> Response:
    return Response(200, text.encode(), "text/csv; charset=utf-8",
                    {"Content-Disposition": f"attachment; filename={filename}"})


def file(path: Path, ctype: str, max_age: int = 0) -> Response:
    cache = f"public, max-age={max_age}" if max_age else "no-cache"
    return Response(200, Path(path).read_bytes(), ctype, {"Cache-Control": cache})


def route(method: str, pattern: str) -> Callable[[RouteFn], RouteFn]:
    compiled = re.compile(pattern)

    def register(fn: RouteFn) -> RouteFn:
        _ROUTES.append((method.upper(), compiled, fn))
        return fn

    return register


def on_startup(fn: Callable[[Any], None]) -> Callable[[Any], None]:
    """Run fn(repo) once when the server starts, before it listens (schema setup and the like)."""
    _STARTUP.append(fn)
    return fn


def resolve(method: str, path: str) -> Optional[Tuple[RouteFn, "re.Match[str]"]]:
    for route_method, pattern, fn in _ROUTES:
        if route_method == method:
            match = pattern.fullmatch(path)
            if match:
                return fn, match
    return None


def startup_hooks() -> List[Callable[[Any], None]]:
    return list(_STARTUP)
