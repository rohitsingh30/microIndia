"""Brand-readiness signals: is this creator already doing brand deals?

A small creator who already runs paid partnerships or disclosed promos is more
valuable to brands than a bigger one who never has. Computed from data already
captured; no extra page loads.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable

DISCLOSURE_RE = re.compile(
    r"#(ad|ads|sponsored|sponsor|collab|collaboration|partner|paidpartnership|gifted|affiliate)\b"
    r"|paid partnership|in partnership with|promo code|use (my )?code|discount code|coupon code|sponsored by",
    re.I,
)
CONTACT_RE = re.compile(
    r"\b(collab|collabs|collaborations?|business enquir|business inquir|for (paid )?promotions?|pr (package|friendly)"
    r"|brand deals?|dm for|mail for|email for|work with me)\b|[\w.+-]+@[\w-]+\.[a-z]{2,}",
    re.I,
)


def brand_signals(profile: Dict[str, Any], posts: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    posts = list(posts)
    paid = sum(1 for post in posts if post.get("is_paid_partnership"))
    disclosed = sum(
        1 for post in posts
        if not post.get("is_paid_partnership") and DISCLOSURE_RE.search(str(post.get("caption_text") or ""))
    )
    bio = " ".join(str(profile.get(key) or "") for key in ("bio_text", "external_url"))
    contact = bool(CONTACT_RE.search(bio))
    brand_posts = paid + disclosed
    return {
        "brand_posts": brand_posts,
        "paid_partnerships": paid,
        "disclosed_promos": disclosed,
        "open_to_collabs": contact,
        # Already doing deals, or openly asking for them with a contact.
        "brand_ready": brand_posts > 0,
    }
