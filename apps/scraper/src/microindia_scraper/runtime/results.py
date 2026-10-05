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


@dataclass
class Offline:
    """This machine lost its network. Not the task's fault: the attempt is given back and every
    runner pauses until the network answers again."""

    reason: str


Result = Union[Done, Retry, Skip, Fail, AuthBlocked, Offline]

# Chromium net errors that mean *we* are offline, not that Instagram refused us.
OFFLINE_MARKERS = (
    "ERR_INTERNET_DISCONNECTED",
    "ERR_NAME_NOT_RESOLVED",
    "ERR_NETWORK_CHANGED",
    "ERR_ADDRESS_UNREACHABLE",
    "ERR_NETWORK_IO_SUSPENDED",
    "ERR_NETWORK_ACCESS_DENIED",
    "ERR_PROXY_CONNECTION_FAILED",
)


def is_offline_error(text: Optional[str]) -> bool:
    return bool(text) and any(marker in text for marker in OFFLINE_MARKERS)


class AuthRequired(RuntimeError):
    """Raise from page code when Instagram shows a login/challenge page."""
