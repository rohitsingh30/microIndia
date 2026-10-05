"""Handler registry: ``@handler("scrape.profile")`` turns a function into a task kind."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from .results import Result


@dataclass
class TaskContext:
    """What a handler gets besides its task: a tab (or None) and shared services."""

    page: Any
    browser: Any
    database: str
    tasks: Any  # TaskStore
    log: Callable[..., None] = field(default=lambda **_: None)


HandlerFn = Callable[[TaskContext, Dict[str, Any]], Awaitable[Result]]


@dataclass
class HandlerSpec:
    kind: str
    fn: HandlerFn
    needs_page: bool = True
    timeout_seconds: float = 600.0


_HANDLERS: Dict[str, HandlerSpec] = {}


def handler(kind: str, *, needs_page: bool = True, timeout_seconds: float = 600.0) -> Callable[[HandlerFn], HandlerFn]:
    def register(fn: HandlerFn) -> HandlerFn:
        if kind in _HANDLERS and _HANDLERS[kind].fn is not fn:
            raise ValueError(f"handler already registered for {kind!r}")
        _HANDLERS[kind] = HandlerSpec(kind, fn, needs_page, timeout_seconds)
        return fn

    return register


def registered_kinds() -> List[str]:
    return sorted(_HANDLERS)


def get_handler(kind: str) -> Optional[HandlerSpec]:
    return _HANDLERS.get(kind)


def handlers_for(patterns: Iterable[str]) -> List[str]:
    """Expand glob patterns like ``source.*`` into registered kinds."""
    selected = {
        kind
        for pattern in patterns
        for kind in _HANDLERS
        if fnmatch.fnmatchcase(kind, pattern.strip())
    }
    return sorted(selected)
