"""Creator insights and fit scoring for the dashboard. Pure functions over captured data."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, Iterable, List, Optional

from .brand import DISCLOSURE_RE

from .constants import COHORT_MAX_FOLLOWERS, COHORT_MIN_FOLLOWERS, DISPLAY_BANDS as BANDS


def band_of(followers: Optional[float]) -> str:
    for low, high, label in BANDS:
        if followers is not None and low <= followers < high:
            return label
    return "unknown"


def _ts(value: Any) -> Optional[float]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()
    except ValueError:
        return None


def _med(values: Iterable[Optional[float]]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    return median(clean) if clean else None


def _is_sponsored(post: Dict[str, Any]) -> bool:
    return bool(post.get("is_paid_partnership") or DISCLOSURE_RE.search(str(post.get("caption_text") or "")))


def creator_insights(creator: Dict[str, Any], posts: List[Dict[str, Any]], peers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """What a brand would want to know before reaching out."""
    followers = creator.get("followers") or 0
    rows = []
    for post in posts:
        likes, comments, views = post.get("like_count"), post.get("comment_count"), post.get("view_count")
        engagement = (likes or 0) + (comments or 0) if likes is not None else None
        rows.append({
            "permalink": post.get("permalink"),
            "type": post.get("content_type") or "post",
            "caption": (post.get("caption_text") or "")[:220],
            "published_ts": _ts(post.get("published_at")),
            "likes": likes, "comments": comments, "views": views,
            "engagement": engagement,
            "rate": engagement / followers if engagement is not None and followers else None,
            "sponsored": _is_sponsored(post),
            "paid_partnership": bool(post.get("is_paid_partnership")),
            "hashtags": [str(tag).lower() for tag in post.get("hashtags") or []],
            "pinned": bool(post.get("is_pinned")),
        })
    scored = [row for row in rows if row["rate"] is not None and not row["pinned"]]
    er = _med(row["rate"] for row in scored)

    # Benchmark against creators of a similar size.
    band = band_of(followers)
    peer_rates = [p["engagement_rate"] for p in peers if band_of(p.get("followers")) == band and p.get("engagement_rate") is not None]
    peer_median = _med(peer_rates)
    percentile = (sum(1 for rate in peer_rates if rate <= er) / len(peer_rates)) if er is not None and peer_rates else None

    # Format performance.
    by_type: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in scored:
        by_type[row["type"]].append(row)
    formats = [
        {"type": kind, "posts": len(items), "median_engagement": _med(r["engagement"] for r in items),
         "median_rate": _med(r["rate"] for r in items), "median_views": _med(r["views"] for r in items)}
        for kind, items in sorted(by_type.items(), key=lambda kv: -len(kv[1]))
    ]
    comparable = [f for f in formats if f["posts"] >= 3]

    # Sponsored vs organic: does their audience still engage with ads?
    sponsored = [r for r in scored if r["sponsored"]]
    organic = [r for r in scored if not r["sponsored"]]
    sponsored_lift = None
    if sponsored and organic and _med(r["rate"] for r in organic):
        sponsored_lift = _med(r["rate"] for r in sponsored) / _med(r["rate"] for r in organic) - 1

    # Cadence.
    dated = sorted((r["published_ts"] for r in rows if r["published_ts"] and not r["pinned"]), reverse=True)
    cadence = None
    if len(dated) >= 2:
        span_days = max(1.0, (dated[0] - dated[-1]) / 86400)
        gaps = [(dated[i] - dated[i + 1]) / 86400 for i in range(len(dated) - 1)]
        cadence = {
            # From the typical gap, so an old pinned or archived post can't skew it.
            "posts_per_week": round(7 / max(0.25, median(gaps)), 1),
            "median_gap_days": round(median(gaps), 1),
            "last_post_days_ago": round((datetime.now(timezone.utc).timestamp() - dated[0]) / 86400, 1),
            "span_days": round(span_days),
        }

    # Conversation quality: comments per 100 likes.
    talk = _med((r["comments"] or 0) / r["likes"] * 100 for r in scored if r["likes"])
    reach = _med(r["views"] / followers for r in scored if r["views"] and followers)

    # Hashtags that travel with above-median engagement.
    tag_rates: Dict[str, List[float]] = defaultdict(list)
    for row in scored:
        for tag in set(row["hashtags"]):
            tag_rates[tag].append(row["rate"])
    hashtags = sorted(
        ({"tag": tag, "posts": len(rates), "median_rate": median(rates),
          "lift": (median(rates) / er - 1) if er else None} for tag, rates in tag_rates.items() if len(rates) >= 2),
        key=lambda item: (item["lift"] or 0), reverse=True,
    )[:8]

    ranked = sorted(scored, key=lambda r: r["rate"], reverse=True)
    consistency = None
    if len(scored) >= 4 and er:
        top_share = sum(r["engagement"] for r in ranked[:2]) / max(1, sum(r["engagement"] for r in scored))
        consistency = round(1 - top_share, 2)  # high = engagement spread across posts, not 1–2 hits

    return {
        "band": band,
        "engagement_rate": er,
        "peer_median_rate": peer_median,
        "peer_percentile": percentile,
        "peer_count": len(peer_rates),
        "formats": formats,
        "sponsored_posts": len(sponsored),
        "sponsored_share": len(sponsored) / len(scored) if scored else None,
        "sponsored_lift": sponsored_lift,
        "cadence": cadence,
        "comments_per_100_likes": talk,
        "reel_reach": reach,
        "consistency": consistency,
        "hashtags": hashtags,
        "best_posts": ranked[:3],
        "weakest_posts": ranked[-2:] if len(ranked) > 4 else [],
        "timeline": sorted(
            ({"ts": r["published_ts"], "engagement": r["engagement"], "type": r["type"], "sponsored": r["sponsored"]}
             for r in rows if r["published_ts"] and r["engagement"] is not None),
            key=lambda item: item["ts"],
        ),
        "summary": _summary(creator, er, peer_median if len(peer_rates) >= 10 else None, percentile, comparable,
                            sponsored, sponsored_lift, cadence, talk),
    }


def _summary(creator, er, peer_median, percentile, formats, sponsored, lift, cadence, talk) -> List[str]:
    """Plain-language takeaways, most useful first."""
    lines: List[str] = []
    if er is not None and peer_median:
        ratio = er / peer_median
        if ratio >= 1.5:
            top = max(1, round((1 - (percentile or 0)) * 100))
            lines.append(f"Engagement is {min(ratio, 20):.1f}× the median for creators this size (top {top}%)." if ratio < 20
                         else f"Engagement far above creators this size (top {top}%); check for a viral outlier.")
        elif ratio <= 0.6:
            lines.append(f"Engagement is below peers: {ratio:.1f}× the median for this size.")
        else:
            lines.append("Engagement is in line with creators of a similar size.")
    if len(formats) >= 2 and formats[0]["median_engagement"] and formats[1]["median_engagement"]:
        best = max(formats, key=lambda f: f["median_engagement"] or 0)
        worst = min(formats, key=lambda f: f["median_engagement"] or 0)
        if best is not worst and worst["median_engagement"]:
            lines.append(f"{best['type'].title()}s get {best['median_engagement'] / worst['median_engagement']:.1f}× the engagement of {worst['type']}s.")
    if sponsored:
        if lift is not None:
            direction = "holds up well" if lift > -0.25 else "drops noticeably"
            lines.append(f"Already ran {len(sponsored)} sponsored post(s); audience response {direction} on ads ({lift:+.0%} vs organic).")
        else:
            lines.append(f"Already ran {len(sponsored)} sponsored post(s).")
    elif creator.get("open_to_collabs"):
        lines.append("No sponsored posts yet, but the bio invites collaborations.")
    if cadence:
        if cadence["last_post_days_ago"] > 30:
            lines.append(f"Inactive lately: last post {cadence['last_post_days_ago']:.0f} days ago.")
        else:
            lines.append(f"Posts about {cadence['posts_per_week']} times a week; last post {cadence['last_post_days_ago']:.0f} days ago.")
    if talk is not None and talk >= 5:
        lines.append(f"Strong conversation: {talk:.0f} comments per 100 likes.")
    return lines


def fit(creator: Dict[str, Any], criteria: Dict[str, Any]) -> Dict[str, Any]:
    """Score 0–100 for "is this who we're looking for", with the reasons."""
    reasons, misses = [], []
    if creator.get("excluded_reason") or creator.get("eligible") is False:
        return {"score": 0, "reasons": [], "misses": [creator.get("excluded_reason") or "out of scope"]}
    score = 0.0
    followers = creator.get("followers")
    low, high = criteria.get("min_followers", COHORT_MIN_FOLLOWERS), criteria.get("max_followers", COHORT_MAX_FOLLOWERS)
    if followers is not None and low <= followers <= high:
        score += 30
        reasons.append(f"{band_of(followers)} followers")
    else:
        misses.append("outside follower range")
    if creator.get("india_signals"):
        score += 20
        reasons.append("Indian (" + creator["india_signals"][0].split(":")[0] + ")")
    elif criteria.get("india"):
        misses.append("no India evidence yet")
    er = creator.get("engagement_rate")
    if er is not None:
        score += min(25.0, er / 0.06 * 25)  # 6%+ engagement earns full marks
        if er >= 0.03:
            reasons.append(f"{er * 100:.1f}% engagement")
    if creator.get("brand_ready"):
        score += 15
        reasons.append(f"{creator.get('brand_posts')} brand post(s)")
    elif creator.get("open_to_collabs"):
        score += 7
        reasons.append("open to collabs")
    if creator.get("captured_ts") and (datetime.now(timezone.utc).timestamp() - creator["captured_ts"]) < 14 * 86400:
        score += 5
    if (creator.get("completeness") or 0) >= 65:
        score += 5
    return {"score": round(min(100.0, score)), "reasons": reasons, "misses": misses}


_WORD = re.compile(r"[a-z]+")
