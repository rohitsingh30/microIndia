"""Typed records used by the incremental capture store."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class CaptureStatus(str, Enum):
    RUNNING = "running"
    COMPLETE = "complete"
    PARTIAL = "partial"
    NEEDS_MANUAL_AUTH = "needs_manual_auth"
    FAILED = "failed"
    QUARANTINED = "quarantined"


@dataclass
class ProfileCapture:
    capture_id: str
    candidate_key: str
    profile_url: str
    captured_at: str
    schema_version: str
    status: CaptureStatus = CaptureStatus.RUNNING
    requested_content_count: int = 18
    observed_content_count: int = 0
    last_completed_index: int = 0
    missing_fields: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    completeness_score: Optional[float] = None


@dataclass
class ProfileObservation:
    capture_id: str
    handle: Optional[str] = None
    profile_url: Optional[str] = None
    display_name: Optional[str] = None
    bio_text: Optional[str] = None
    external_url: Optional[str] = None
    follower_count: Optional[int] = None
    following_count: Optional[int] = None
    post_count: Optional[int] = None
    is_verified: Optional[bool] = None
    is_private: Optional[bool] = None
    account_type: Optional[str] = None
    business_category: Optional[str] = None
    location_text: Optional[str] = None
    language_signals: List[str] = field(default_factory=list)
    commercial_signals: Dict[str, bool] = field(default_factory=dict)
    ai_signals: List[str] = field(default_factory=list)
    ai_score: float = 0.0
    ai_label: str = "unknown"
    ai_evidence: List[str] = field(default_factory=list)
    observed_at: Optional[str] = None


@dataclass
class ContentObservation:
    capture_id: str
    content_index: int
    permalink: Optional[str] = None
    platform_content_id: Optional[str] = None
    content_type: str = "unknown"
    published_at: Optional[str] = None
    is_pinned: Optional[bool] = None
    is_collaboration: Optional[bool] = None
    is_paid_partnership: Optional[bool] = None
    caption_text: Optional[str] = None
    text_content: Optional[str] = None
    hashtags: List[str] = field(default_factory=list)
    mentions: List[str] = field(default_factory=list)
    like_count: Optional[int] = None
    comment_count: Optional[int] = None
    view_count: Optional[int] = None
    location_text: Optional[str] = None
    metric_availability: str = "unavailable"
    ai_signals: List[str] = field(default_factory=list)
    ai_score: float = 0.0
    ai_label: str = "unknown"
    ai_evidence: List[str] = field(default_factory=list)
    observed_at: Optional[str] = None
    item_status: str = "observed"
    warning: Optional[str] = None


def jsonable(value: Any) -> Any:
    """Convert dataclasses/enums into JSON-compatible values."""
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {key: jsonable(item) for key, item in value.__dict__.items()}
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    return value