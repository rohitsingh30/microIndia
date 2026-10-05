"""Typed handler results. A handler returns exactly one of these."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


@dataclass
class FollowUp:
    """A task a handler wants queued. ``key`` dedupes within a kind."""

    kind: str
    key: str
    payload: Dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    delay_seconds: float = 0.0


@dataclass
class Done:
    data: Dict[str, Any] = field(default_factory=dict)
    follow_ups: List[FollowUp] = field(default_factory=list)


@dataclass
class Retry:
    reason: str
    after_seconds: Optional[float] = None  # None = exponential backoff by attempt


@dataclass
class Skip:
    """Terminal, expected: the target is not worth processing (private, out of band...)."""

    reason: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Fail:
    """Terminal, unexpected: retrying will not help."""

    reason: str


@dataclass
class AuthBlocked:
    """The signed-in session hit a login/challenge page. Pauses every runner."""

    reason: str


Result = Union[Done, Retry, Skip, Fail, AuthBlocked]


class AuthRequired(RuntimeError):
    """Raise from page code when Instagram shows a login/challenge page."""
