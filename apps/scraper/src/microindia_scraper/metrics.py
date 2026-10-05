"""Derived metrics calculated from already-persisted content observations."""

from __future__ import annotations

import json
from statistics import mean, median
from typing import Any, Dict, List, Optional

from .constants import METRIC_SAMPLE_LIMIT


def _number(values: List[Optional[int]]) -> List[float]:
    return [float(value) for value in values if value is not None]


def calculate_metrics(
    profile_payload: Dict[str, Any],
    content_payloads: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Calculate metrics without requiring another browser visit.

    Content is assumed to be ordered from newest to oldest. Missing metrics are
    excluded from aggregates rather than being treated as zero.
    """
    eligible = [
        item
        for item in content_payloads
        if item.get("item_status") == "observed" and item.get("is_pinned") is not True
    ][:METRIC_SAMPLE_LIMIT]
    followers = profile_payload.get("follower_count")
    likes = _number([item.get("like_count") for item in eligible])
    comments = _number([item.get("comment_count") for item in eligible])
    views = _number(
        [item.get("view_count") for item in eligible if item.get("content_type") == "reel"]
    )

    engagement_rates = []
    if followers:
        for item in eligible:
            like_count = item.get("like_count")
            comment_count = item.get("comment_count")
            if like_count is not None and comment_count is not None:
                engagement_rates.append((like_count + comment_count) / followers)

    return {
        "eligible_item_count": len(eligible),
        "sample_limit": METRIC_SAMPLE_LIMIT,
        "average_likes": mean(likes) if likes else None,
        "median_likes": median(likes) if likes else None,
        "average_comments": mean(comments) if comments else None,
        "median_comments": median(comments) if comments else None,
        "average_reel_views": mean(views) if views else None,
        "median_reel_views": median(views) if views else None,
        # Median: one viral reel must not turn a 2% creator into a 1,600% one.
        "engagement_rate_by_followers": median(engagement_rates) if engagement_rates else None,
        "average_engagement_rate": mean(engagement_rates) if engagement_rates else None,
        "metric_confidence": _confidence(len(eligible), len(engagement_rates)),
    }


def _confidence(item_count: int, complete_metric_count: int) -> str:
    if item_count >= METRIC_SAMPLE_LIMIT and complete_metric_count >= 8:
        return "high"
    if item_count >= 8 and complete_metric_count >= 4:
        return "medium"
    return "low"


def decode_snapshot(payload: str) -> Dict[str, Any]:
    return json.loads(payload)