"""Eligibility rules for the India human micro-creator pilot cohort."""

from __future__ import annotations

import re
from typing import List, Tuple

from .models import ProfileObservation


from .constants import BAND_LABEL, SCRAPE_MAX_FOLLOWERS, SCRAPE_MIN_FOLLOWERS

# Hard scope: clearly outside these numbers a profile is of no use to us.
MICRO_MIN_FOLLOWERS = SCRAPE_MIN_FOLLOWERS
MICRO_MAX_FOLLOWERS = SCRAPE_MAX_FOLLOWERS

# These are deliberately conservative. They reject obvious channels/pages while
# leaving ambiguous profiles for human review rather than silently accepting them.
# Bio words that only businesses use. Instagram's own category label ("Restaurant",
# "Shopping & retail") is the main business signal (account_type); words creators use
# about themselves ("brand partnerships", "shop my looks", "official") are excluded.
# Small businesses (a local bakery, a saree shop, a coaching academy) are kept and tagged;
# big ones and publishers are dropped.
SMALL_BUSINESS_MAX = 50_000
BUSINESS_TERMS = {"restaurant", "cafe", "bakery", "academy", "pvt", "ltd", "llp"}
PUBLISHER_TERMS = {"agency", "magazine", "news", "foundation", "organization", "organisation"}
NON_HUMAN_TERMS = BUSINESS_TERMS | PUBLISHER_TERMS  # kept for callers that check both
# Phrases only pages use about themselves.
NON_HUMAN_PHRASES = re.compile(
    r"\bofficial (page|channel)\b|\bmedia (channel|house|company|page|network)\b"
    r"|\bnews (portal|channel|page)\b|\b(food|travel|fashion) (network|portal|magazine)\b",
    re.I,
)
# AI-generated personas and repost/aggregator pages are never what a brand wants.
AI_NAME = re.compile(
    r"(?:^|[._\s-])ai(?:[._\s-]|$)(?=.*?(creator|art|artist|girl|boy|model|influencer|generated|world|studio|"
    r"universe|baby|beauty|queen|diaries|vibes|visuals|lens))|(?:creator|art|girl|model|influencer)[._\s-]?ai\b"
    r"|\bvirtual (influencer|creator|model|human)\b|\bai[- ]?(generated|influencer|model|girl|art|artist|creator)\b"
    r"|\bdigital human\b|\bmidjourney\b|\bstable ?diffusion\b|\bai ?(?:girl|babe|beauty)s?\b",
    re.I,
)
REPOST_PAGE = re.compile(
    r"dm (?:for|4) (?:credit|credits|removal|remove)|credit[s]? (?:go(?:es)? )?to (?:the )?(?:respective|original)"
    r"|(?:all )?rights? (?:belong|reserved) to (?:the )?(?:respective|original)|respective owners"
    r"|no copyright infringement|not (?:my|our) (?:content|videos)|\bfan ?page\b|\brepost(?:s|ing)? (?:page|account)\b",
    re.I,
)


# Collective handles ("indian_fashion_bloggers", "foodie.hub", "mumbai_updates") are pages, not people.
AGGREGATOR_HANDLE = re.compile(
    r"(bloggers|influencers|creators|community|magazine|updates|feature[sd]?|reposts?|network|official_?page|"
    r"\bhub\b|_hub|hub_|\.hub|daily_?news|_news\b|world_?of_|club_?official)",
    re.I,
)


def ai_or_repost_reason(handle: str, name: str, bio: str) -> str:
    """Reason a profile is an AI persona or a repost page, or '' if neither (cheap, text only)."""
    identity = f"{handle} {name}"
    if AI_NAME.search(identity) or AI_NAME.search(bio or ""):
        return "AI-generated persona"
    if REPOST_PAGE.search(bio or "") or AGGREGATOR_HANDLE.search(handle or ""):
        return "repost/aggregator page"
    return ""


NON_HUMAN_NAME = re.compile(r"\b(network|media|news|official|pvt|ltd)\b", re.I)

INDIA_TERMS = {
    "india", "bharat", "desi", "lucknow", "chandigarh", "indore", "kochi", "cochin", "goa", "surat",
    "nagpur", "bhopal", "coimbatore", "guwahati", "bhubaneswar", "patna", "vizag", "visakhapatnam",
    "mysore", "mysuru", "dehradun", "amritsar", "noida", "gurgaon", "gurugram", "thane", "kanpur",
    "varanasi", "trivandrum", "thiruvananthapuram", "madurai", "udaipur", "jodhpur", "ludhiana",
    "rajasthan", "karnataka", "odisha", "assam", "bihar", "uttarakhand", "himachal", "kashmir",
    "telangana", "andhra", "bengal", "gujarati", "punjabi", "rajasthani", "hyderabadi", "mumbaikar",
    "dilli", "delhiite", "bangalorean", "chennaite", "keralite", "mallu", "south indian", "north indian",
    "indian",
    "delhi",
    "mumbai",
    "pune",
    "bangalore",
    "bengaluru",
    "hyderabad",
    "chennai",
    "kolkata",
    "ahmedabad",
    "jaipur",
    "kerala",
    "gujarat",
    "punjab",
    "maharashtra",
    "tamil",
    "telugu",
    "malayalam",
    "hindi",
    "kannada",
    "marathi",
    "bengali",
}


def _plain(value: str) -> str:
    """Fold Unicode 'fancy text' (ᴅᴍ ꜰᴏʀ ᴄʀᴇᴅɪᴛ, 𝔣𝔬𝔬𝔡) back to ASCII so text rules still match."""
    import unicodedata

    folded = unicodedata.normalize("NFKC", value)
    small_caps = str.maketrans("ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡʏᴢ", "abcdefghijklmnopqrstuvwyz")
    return folded.translate(small_caps)


def _words(value: str) -> set[str]:
    return set(re.findall(r"[a-z][a-z&'-]+", value.lower()))


# Devanagari, Bengali, Gurmukhi, Gujarati, Odia, Tamil, Telugu, Kannada, Malayalam.
_INDIC_SCRIPT = re.compile(r"[\u0900-\u0d7f]")
_HASHTAG_TERMS = sorted((term for term in INDIA_TERMS if len(term) >= 4 and " " not in term), key=len, reverse=True)


def india_evidence(text: str) -> List[str]:
    """Why a text looks Indian: script, rupee/+91, place or language words, or #cityfood-style hashtags."""
    found: List[str] = []
    if _INDIC_SCRIPT.search(text):
        found.append("indic script")
    if "₹" in text or re.search(r"\+91[\s-]?\d", text):
        found.append("rupee/+91")
    words = _words(text)
    places = sorted(words & INDIA_TERMS) + [term for term in INDIA_TERMS if " " in term and term in text.lower()]
    if places:
        found.append("terms: " + ", ".join(places[:4]))
    tags = re.findall(r"#([a-z0-9_]+)", text.lower())
    tagged = sorted({term for tag in tags for term in _HASHTAG_TERMS if term in tag})
    if tagged:
        found.append("hashtags: " + ", ".join(tagged[:4]))
    return found


def human_indian_micro_creator_reasons(profile: ProfileObservation, content_text: str = "") -> List[str]:
    """Hard rejection reasons only: private, outside the follower band, clearly a business.

    India evidence, location, post count and completeness are signals, not gates:
    see ``india_signals``. ``content_text`` is accepted for callers that pass posts.
    """

    reasons: List[str] = []
    if profile.is_private is True:
        reasons.append("private account")
    if profile.follower_count is None:
        reasons.append("follower count unavailable")
    elif not MICRO_MIN_FOLLOWERS <= profile.follower_count <= MICRO_MAX_FOLLOWERS:
        reasons.append("outside follower range (500-1M)")

    publisher = publisher_signals(profile)
    if publisher:
        reasons.append(f"publisher/media page: {', '.join(publisher)}")

    special = ai_or_repost_reason(profile.handle or "", profile.display_name or "", _plain(profile.bio_text or ""))
    label = (profile.account_type or "").lower()
    if special:
        reasons.append(special)
    elif profile.ai_label == "ai_dominant" or "ai-generated" in label:
        reasons.append("AI-generated persona")

    if not publisher and account_kind(profile) == "business" and (profile.follower_count or 0) > SMALL_BUSINESS_MAX:
        reasons.append(f"big business (over {SMALL_BUSINESS_MAX // 1000}K followers)")
    return reasons


def _identity_text(profile: ProfileObservation) -> str:
    return " ".join(value or "" for value in (profile.handle, profile.display_name, profile.bio_text, profile.business_category))


def publisher_signals(profile: ProfileObservation) -> List[str]:
    """News/media/agency/official pages: never what a brand means by a creator."""
    text = _identity_text(profile)
    found = sorted(_words(text) & PUBLISHER_TERMS)
    phrase = NON_HUMAN_PHRASES.search(text)
    if phrase:
        found.append(phrase.group(0).lower())
    name = NON_HUMAN_NAME.search(profile.display_name or "")
    if name and name.group(0).lower() in {"network", "media", "news", "official"}:
        found.append("page name: " + name.group(0).lower())
    label = (profile.account_type or "").lower()
    if label in {"organization", "media"}:
        found.append(f"label: {label}")
    return found


def account_kind(profile: ProfileObservation) -> str:
    """'business' (Instagram business label or shop/restaurant words) or 'creator'."""
    label = (profile.account_type or "").lower()
    if label == "business" or (_words(_identity_text(profile)) & BUSINESS_TERMS):
        return "business"
    return "creator"


def is_human_indian_micro_creator(profile: ProfileObservation) -> bool:
    return not human_indian_micro_creator_reasons(profile)


def eligibility_error(profile: ProfileObservation) -> str:
    reasons = human_indian_micro_creator_reasons(profile)
    return "eligible" if not reasons else "; ".join(reasons)


def eligibility_summary(profile: ProfileObservation) -> Tuple[bool, List[str]]:
    reasons = human_indian_micro_creator_reasons(profile)
    return not reasons, reasons

def india_signals(profile: ProfileObservation, content_text: str = "") -> List[str]:
    """Evidence that a creator is Indian (empty = unknown, not rejected)."""
    indic_languages = {"hi", "ta", "te", "ml", "bn", "mr", "kn", "gu", "pa"}
    found = [f"language: {code}" for code in sorted(set(profile.language_signals or []) & indic_languages)]
    text = " ".join(value or "" for value in (profile.handle, profile.display_name, profile.bio_text,
                                               profile.location_text, content_text))
    return found + india_evidence(text)
