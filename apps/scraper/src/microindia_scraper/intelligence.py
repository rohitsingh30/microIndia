"""Explainable, offline creator and individual-post feature derivation."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, Iterable, List, Optional


# v2: languages, topics, formats and CTAs come only from creator-authored text (the caption).
# v1 read the whole page body, whose footer lists every UI language (हिन्दी, தமிழ், తెలుగు, മലയാളം...)
# and whose comments/buttons ("Follow", "Reply", "Get the app") faked CTAs and topics.
FEATURE_VERSION = "features-v2"
REEL_ANALYSIS_VERSION = "reel-analysis-v2"

# Script -> language code. Devanagari is reported as "hi" (Hindi, the dominant Devanagari language here).
SCRIPT_LANGUAGES = (
    ("hi", r"[\u0900-\u097f]"),
    ("bn", r"[\u0980-\u09ff]"),
    ("pa", r"[\u0a00-\u0a7f]"),
    ("gu", r"[\u0a80-\u0aff]"),
    ("ta", r"[\u0b80-\u0bff]"),
    ("te", r"[\u0c00-\u0c7f]"),
    ("kn", r"[\u0c80-\u0cff]"),
    ("ml", r"[\u0d00-\u0d7f]"),
    ("en", r"[A-Za-z]"),
)

# Instagram's footer: the language picker lists every UI language in its own script, after
# links like "Meta / About / Blog". It is on every page body, so it must never count as a signal.
INSTAGRAM_UI_LANGUAGES = frozenset("""
English|Afrikaans|العربية|Čeština|Dansk|Deutsch|Ελληνικά|English (UK)|Español (España)|Español|فارسی|Suomi|Français|
עברית|Bahasa Indonesia|Italiano|日本語|한국어|Bahasa Melayu|Norsk|Nederlands|Polski|Português (Brasil)|
Português (Portugal)|Русский|Svenska|ภาษาไทย|Filipino|Türkçe|中文(简体)|中文(台灣)|বাংলা|ગુજરાતી|हिन्दी|Hrvatski|
Magyar|ಕನ್ನಡ|മലയാളം|मराठी|नेपाली|ਪੰਜਾਬੀ|සිංහල|Slovenčina|தமிழ்|తెలుగు|اردو|Tiếng Việt|中文(香港)|Български|
Français (Canada)|Română|Српски|Українська""".replace("\n", "").split("|"))
_FOOTER_START = re.compile(r"^(?:More posts from .+|Meta)$")


def strip_instagram_chrome(text: str) -> str:
    """Drop Instagram's footer (links + language list) from page text."""
    kept: List[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if _FOOTER_START.match(stripped):
            break  # everything after "More posts from …" / "Meta" is footer
        if stripped in INSTAGRAM_UI_LANGUAGES:
            continue
        kept.append(line)
    return "\n".join(kept)


def language_signals(text: str) -> List[str]:
    """Languages by script in creator-authored text. Never pass whole-page text: use the caption."""
    cleaned = strip_instagram_chrome(text)
    cleaned = re.sub(r"[#@][\w.]+", " ", cleaned)  # hashtags and handles say little about spoken language
    cleaned = re.sub(r"https?://\S+", " ", cleaned)
    return sorted({code for code, pattern in SCRIPT_LANGUAGES if re.search(pattern, cleaned)})


def creator_text(post: Dict[str, Any]) -> str:
    """The words the creator wrote for this post. ``text_content`` is the whole page body
    (other people's comments, buttons, the footer) and is deliberately not used."""
    return strip_instagram_chrome(str(post.get("caption_text") or "")).strip()


def _term_count(lowered: str, term: str) -> int:
    """Whole-word occurrences ("ai" must not match "said"/"chai", "run" not "brunch")."""
    if not re.match(r"\w", term):  # emoji and punctuation terms
        return lowered.count(term)
    return len(re.findall(r"(?<![\w])" + re.escape(term) + r"(?![\w])", lowered))

TOPIC_TERMS = {
    "food": ("food", "recipe", "cook", "cooking", "bake", "restaurant", "cafe", "meal"),
    "fashion": ("fashion", "style", "outfit", "dress", "makeup", "beauty", "skincare"),
    "fitness": ("fitness", "workout", "gym", "yoga", "run", "health", "wellness"),
    "travel": ("travel", "trip", "explore", "hotel", "tour", "vacation", "itinerary"),
    "technology": ("tech", "software", "developer", "coding", "gadget", "ai", "app"),
    "finance": ("finance", "invest", "money", "crypto", "stocks", "saving", "loan"),
    "lifestyle": ("lifestyle", "home", "parenting", "books", "art", "photography"),
}

# Whole-word stems: substring counting made "ai" match "said"/"chai" and turned
# food creators into "technology".
CATEGORY_STEMS = {
    "food": ("food", "foodie", "recipe", "cook", "cooking", "chef", "bake", "baking", "biryani", "kitchen",
             "restaurant", "cafe", "street food", "dessert", "eat", "eats", "eating", "thali", "chai", "snack"),
    "fashion": ("fashion", "style", "outfit", "ootd", "saree", "lehenga", "kurti", "makeup", "beauty",
                "skincare", "hair", "jewellery", "jewelry"),
    "fitness": ("fitness", "workout", "gym", "yoga", "health", "wellness", "fit", "run", "running", "nutrition"),
    "travel": ("travel", "traveller", "traveler", "trip", "wanderlust", "explore", "tourism", "trek", "trekking",
               "backpacking", "itinerary", "vacation"),
    "technology": ("tech", "techie", "software", "developer", "coding", "gadget", "gadgets", "smartphone",
                   "unboxing", "artificial intelligence", "ai tools"),
    "finance": ("finance", "investing", "investment", "money", "crypto", "stocks", "stock market", "trading",
                "personal finance", "mutual funds"),
    "parenting": ("mom", "mother", "momlife", "parenting", "baby", "kids", "toddler", "dad"),
    "lifestyle": ("lifestyle", "vlog", "vlogger", "daily", "home decor", "books", "art", "artist",
                  "photography", "dance", "comedy"),
}


def category_scores(text: str) -> Dict[str, int]:
    from .niches import niche_scores

    return niche_scores(text)


def primary_category_of(text: str) -> Optional[str]:
    from .niches import primary_niche

    return primary_niche(text)


CTA_TERMS = ("follow", "save", "share", "comment", "subscribe", "dm", "link in bio", "tell me")
FORMAT_TERMS = {
    "tutorial": ("how to", "steps", "tutorial", "tips", "recipe", "guide"),
    "review": ("review", "unboxing", "tested", "worth it", "rating"),
    "storytelling": ("story", "journey", "day in", "experience", "storytime"),
    "comedy": ("comedy", "funny", "meme", "joke", "😂", "🤣"),
    "recommendation": ("best", "recommend", "must try", "places to", "top "),
}

AI_PROFILE_DOMINANT_PATTERNS = {
    "profile_ai_creator": r"\bai\s+(?:creator|artist|influencer)\b",
    "profile_ai_generated_content": r"\bai[-\s]?generated\s+content\b",
    "profile_virtual_influencer": r"\bvirtual\s+(?:influencer|creator)\b",
    "profile_synthetic_media": r"\bsynthetic\s+media\b",
}
AI_HIGH_CONFIDENCE_PATTERNS = {
    "ai_generated": r"\b(?:ai[-\s]?generated|generated\s+with\s+ai|made\s+with\s+ai|created\s+by\s+ai)\b",
    "synthetic_media": r"\bsynthetic\s+media\b",
    "virtual_influencer": r"\bvirtual\s+(?:influencer|creator)\b",
    "midjourney": r"\bmidjourney\b",
    "stable_diffusion": r"\bstable\s*diffusion\b",
    "dall_e": r"\bdall[-\s]?e\b",
    "instagram_ai_label": r"\b(?:ai\s+info|ai[-\s]?label(?:ed)?)\b",
}
AI_LOW_CONFIDENCE_PATTERNS = {
    "ai_hashtag": r"(?<!\w)#ai(?:generated|art)?\b",
    "ai_art": r"\bai\s+art\b",
}


def extract_ai_evidence(text: str, *, profile: bool = False) -> Dict[str, Any]:
    """Extract explicit public text/metadata AI signals without visual inference."""
    source = str(text or "")
    signals: List[str] = []
    evidence: List[str] = []
    high_confidence = False
    for name, pattern in (AI_PROFILE_DOMINANT_PATTERNS.items() if profile else ()):
        match = re.search(pattern, source, re.I)
        if match:
            signals.append(name)
            evidence.append(match.group(0)[:160])
            high_confidence = True
    for name, pattern in AI_HIGH_CONFIDENCE_PATTERNS.items():
        match = re.search(pattern, source, re.I)
        if match:
            signals.append(name)
            evidence.append(match.group(0)[:160])
            high_confidence = True
    for name, pattern in AI_LOW_CONFIDENCE_PATTERNS.items():
        match = re.search(pattern, source, re.I)
        if match:
            signals.append(name)
            evidence.append(match.group(0)[:160])
    signals = sorted(set(signals))
    evidence = list(dict.fromkeys(evidence))
    if not signals:
        return {"ai_signals": [], "ai_score": 0.0, "ai_label": "unknown", "ai_evidence": []}
    if profile and any(signal.startswith("profile_") for signal in signals):
        label, score = "ai_dominant", 1.0
    elif high_confidence:
        label, score = "ai_generated", 0.95
    else:
        label, score = "ai_signal", 0.35
    return {
        "ai_signals": signals,
        "ai_score": score,
        "ai_label": label,
        "ai_evidence": evidence,
    }


def _tokens(text: str) -> List[str]:
    return re.findall(r"#[\w]+", text.lower())


def derive_post_features(post: Dict[str, Any], follower_count: Optional[int] = None) -> Dict[str, Any]:
    caption = post.get("caption_text") or ""
    hashtags = post.get("hashtags") or _tokens(caption)
    likes, comments, views = post.get("like_count"), post.get("comment_count"), post.get("view_count")
    ai = {
        "ai_signals": post.get("ai_signals"),
        "ai_score": post.get("ai_score"),
        "ai_label": post.get("ai_label"),
        "ai_evidence": post.get("ai_evidence"),
    }
    if not ai["ai_signals"]:
        ai = extract_ai_evidence(" ".join(
            value for value in (caption, post.get("text_content"), " ".join(hashtags)) if value
        ))
    result: Dict[str, Any] = {
        "content_type": post.get("content_type"),
        "caption_length": len(caption),
        "hashtag_count": len(hashtags),
        "mention_count": len(post.get("mentions") or []),
        "is_pinned": post.get("is_pinned"),
        "is_collaboration": post.get("is_collaboration"),
        "published_at": post.get("published_at"),
        "commercial_disclosure": bool(re.search(r"#(ad|sponsored|affiliate)\b|paid partnership|promo code", caption, re.I)),
        "like_count": likes,
        "comment_count": comments,
        "view_count": views,
        "metric_availability": post.get("metric_availability"),
        "comment_to_like_ratio": (comments / likes) if likes and comments is not None else None,
        "engagement_rate": ((likes + comments) / follower_count) if follower_count and likes is not None and comments is not None else None,
        "engagement_per_view": ((likes + comments) / views) if views and likes is not None and comments is not None else None,
        **ai,
    }
    if post.get("content_type") == "reel":
        result["reel_analysis"] = analyze_reel(post)
    return result

def analyze_reel(post: Dict[str, Any]) -> Dict[str, Any]:
    """Transparent, rule-based reel signals from the creator's own caption.

    Only creator-authored text counts: the page body (``text_content``) holds Instagram's
    footer language list, buttons and other people's comments. Matching is whole-word.
    """
    caption = creator_text(post)
    text_content = post.get("text_content") or ""
    lowered = caption.lower()
    topic_scores = {
        topic: sum(_term_count(lowered, term) for term in terms)
        for topic, terms in TOPIC_TERMS.items()
    }
    topic = max(topic_scores, key=topic_scores.get) if max(topic_scores.values(), default=0) else "other"
    format_scores = {
        style: sum(_term_count(lowered, term) for term in terms)
        for style, terms in FORMAT_TERMS.items()
    }
    formats = [style for style, score in format_scores.items() if score > 0] or ["general_short_video"]
    cta_matches = [term for term in CTA_TERMS if _term_count(lowered, term)]
    commercial = bool(re.search(r"#(ad|sponsored|affiliate)\b|paid partnership|promo code|\bcollab", lowered))
    first_sentence = re.split(r"[.!?\n]", caption, maxsplit=1)[0].strip()
    return {
        "analysis_version": REEL_ANALYSIS_VERSION,
        "topic": topic,
        "topic_scores": topic_scores,
        "format_signals": formats,
        "language_signals": language_signals(caption),
        "hook_text": first_sentence[:180] or None,
        "hook_length": len(first_sentence),
        "call_to_action": bool(cta_matches),
        "call_to_action_terms": cta_matches,
        "commercial": commercial,
        "text_evidence_chars": len(caption),
        "has_caption": bool(caption),
        "has_extracted_text": bool(text_content.strip()),
        "analysis_confidence": "medium" if len(caption) >= 40 else "low" if caption else "insufficient_text_evidence",
        "evidence_status": "caption" if caption else "metadata_only",
    }


def derive_creator_features(profile: Dict[str, Any], content: Iterable[Dict[str, Any]], metrics: Dict[str, Any]) -> Dict[str, Any]:
    posts = list(content)
    # Creator-authored text only (see creator_text): the page body would add the footer's language list.
    text_by_post = [creator_text(item) for item in posts]
    corpus = " ".join(text_by_post).lower()
    hashtags = [
        tag.lower()
        for item in posts
        for tag in (item.get("hashtags") or _tokens(item.get("caption_text") or ""))
    ]
    scores = category_scores(corpus + " " + " ".join(hashtags))
    primary_category = max(scores, key=scores.get) if scores and max(scores.values()) else "other"
    own_words = " ".join(
        [str(profile.get(key) or "") for key in ("display_name", "bio_text")] + text_by_post
    )
    languages = language_signals(own_words)
    parsed_dates = []
    for item in posts:
        value = item.get("published_at")
        if not value:
            continue
        try:
            parsed_dates.append(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
        except ValueError:
            continue
    parsed_dates.sort()
    latest_published_at = parsed_dates[-1].isoformat() if parsed_dates else None
    posting_window_days = (
        max(0, (parsed_dates[-1] - parsed_dates[0]).total_seconds() / 86_400)
        if len(parsed_dates) >= 2 else None
    )
    top_posts = sorted(
        (
            {
                "permalink": item.get("permalink"),
                "content_type": item.get("content_type"),
                "like_count": item.get("like_count"),
                "comment_count": item.get("comment_count"),
                "view_count": item.get("view_count"),
                "published_at": item.get("published_at"),
                "engagement_total": (item.get("like_count") or 0) + (item.get("comment_count") or 0),
            }
            for item in posts
            if item.get("permalink")
        ),
        key=lambda item: item["engagement_total"],
        reverse=True,
    )[:3]
    commercial_rate = _rate(
        posts,
        lambda item: bool(re.search(
            r"#(ad|sponsored|affiliate)\b|paid partnership|promo code|collab",
            item.get("caption_text") or "",
            re.I,
        )),
    )
    follower_count = profile.get("follower_count")
    ai_items = [item for item in posts if item.get("ai_signals")]
    high_confidence_ai_items = [item for item in posts if float(item.get("ai_score") or 0.0) >= 0.75]
    profile_ai = extract_ai_evidence(
        " ".join(value for value in (
            profile.get("display_name"),
            profile.get("bio_text"),
            profile.get("business_category"),
        ) if value),
        profile=True,
    )
    ai_label = profile_ai["ai_label"]
    if ai_label == "unknown":
        ai_label = "ai_generated" if high_confidence_ai_items else "ai_signal" if ai_items else "unknown"
    ai_confidence = (
        "high" if profile_ai["ai_label"] == "ai_dominant" or len(high_confidence_ai_items) >= 3
        else "medium" if ai_items else "unknown"
    )
    ai_share = len(high_confidence_ai_items) / len(posts) if posts else None
    ai_features = {
        "ai_label": ai_label,
        "ai_share": ai_share,
        "ai_confidence": ai_confidence,
        "ai_evidence_item_count": len(ai_items),
        "high_confidence_ai_item_count": len(high_confidence_ai_items),
        "profile_ai_signals": profile_ai["ai_signals"],
        "profile_ai_evidence": profile_ai["ai_evidence"],
        "evidence_scope": "public_text_and_metadata",
    }
    return {
        "follower_count": follower_count,
        "following_count": profile.get("following_count"),
        "post_count": profile.get("post_count"),
        "follower_band": _band(follower_count),
        "primary_category": primary_category,
        "category_scores": scores,
        "languages": languages,
        "feature_version": FEATURE_VERSION,
        "content_type_mix": _counts(item.get("content_type") for item in posts),
        "post_sample_size": len(posts),
        "posts_with_metrics": sum(1 for item in posts if item.get("like_count") is not None),
        "posts_with_captions": sum(1 for item in posts if item.get("caption_text")),
        "posts_with_dates": len(parsed_dates),
        "commercial_disclosure_rate": commercial_rate,
        "median_likes": _median(item.get("like_count") for item in posts),
        "median_comments": _median(item.get("comment_count") for item in posts),
        "median_views": _median(item.get("view_count") for item in posts),
        "engagement_rate": metrics.get("engagement_rate_by_followers"),
        "metric_confidence": metrics.get("metric_confidence"),
        "activity_signal": "active" if parsed_dates else "unknown",
        "latest_published_at": latest_published_at,
        "posting_window_days": posting_window_days,
        "top_hashtags": [
            {"tag": tag, "count": count}
            for tag, count in Counter(hashtags).most_common(15)
        ],
        "top_posts": top_posts,
        "profile_research": {
            "location_text": profile.get("location_text"),
            "business_category": profile.get("business_category"),
            "account_type": profile.get("account_type"),
            "external_url": profile.get("external_url"),
            # Recomputed from the bio: stored signals on older captures came from the whole page.
            "language_signals": language_signals(" ".join(
                str(profile.get(key) or "") for key in ("display_name", "bio_text"))),
            "commercial_signals": profile.get("commercial_signals") or {},
        },
        "ai_analysis": ai_features,
        "data_quality": "high" if len(posts) >= 12 and metrics.get("metric_confidence") == "high" and len(parsed_dates) >= 8 else "medium" if posts else "low",
        "feature_evidence": {
            "source": "public_profile_and_recent_posts",
            "post_count": len(posts),
            "profile_fields": ["bio", "counts", "location", "language", "commercial_signals"],
            "content_fields": ["caption", "hashtags", "mentions", "published_at", "metrics", "location"],
        },
        "calculated_at": datetime.now(timezone.utc).isoformat(),
    }


def _band(value: Any) -> Optional[str]:
    if value is None:
        return None
    for threshold, label in [(1_000, "nano"), (10_000, "micro"), (100_000, "mid"), (1_000_000, "macro")]:
        if value < threshold:
            return label
    return "mega"


def _median(values: Iterable[Any]) -> Optional[float]:
    numbers = [float(value) for value in values if value is not None]
    return median(numbers) if numbers else None


def _counts(values: Iterable[Any]) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for value in values:
        key = value or "unknown"
        result[key] = result.get(key, 0) + 1
    return result


def _rate(values: List[Any], predicate: Any) -> Optional[float]:
    return sum(1 for value in values if predicate(value)) / len(values) if values else None