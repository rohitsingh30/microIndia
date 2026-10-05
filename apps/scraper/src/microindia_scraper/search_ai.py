"""Chat search: turn a brand's request into Find-page criteria.

    "Hyderabad food creators under 50K who've done brand deals" ->
    {"city": "Hyderabad", "category": "food", "max_followers": 50000, "brand": "1"}

Two engines, same output:
- ``llm``: one Claude (Sonnet) call per message through ``llm.py`` with a JSON schema. Switched on
  with MICROINDIA_LLM=on (see ``llm.brand_ai_enabled``).
- ``rules``: deterministic parser, free and instant; also the fallback whenever
  the LLM is off, fails, or returns something unusable.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

CRITERIA_KEYS = ("q", "city", "category", "language", "min_followers", "max_followers", "min_engagement",
                 "india", "brand", "active_days")
CATEGORIES = ("food", "fashion", "fitness", "travel", "technology", "finance", "parenting", "lifestyle")
CATEGORY_WORDS = {
    "food": ("food", "foodie", "recipe", "cook", "chef", "baking", "restaurant", "biryani", "street food", "dessert"),
    "fashion": ("fashion", "style", "outfit", "saree", "makeup", "beauty", "skincare", "jewellery"),
    "fitness": ("fitness", "gym", "yoga", "workout", "health", "wellness", "nutrition"),
    "travel": ("travel", "trip", "trek", "wanderlust", "tourism"),
    "technology": ("tech", "gadget", "smartphone", "coding", "unboxing"),
    "finance": ("finance", "money", "investing", "stock", "crypto", "trading"),
    "parenting": ("mom", "parenting", "baby", "kids", "dad"),
    "lifestyle": ("lifestyle", "vlog", "home decor", "art", "photography", "dance", "comedy"),
}
LANGUAGES = {"hindi": "hi", "tamil": "ta", "telugu": "te", "malayalam": "ml", "bengali": "bn", "marathi": "mr",
             "kannada": "kn", "gujarati": "gu", "punjabi": "pa"}
CITY_ALIASES = {
    "delhi": "Delhi", "new delhi": "Delhi", "ncr": "Delhi NCR", "noida": "Delhi NCR", "gurgaon": "Delhi NCR",
    "gurugram": "Delhi NCR", "mumbai": "Mumbai", "bombay": "Mumbai", "bangalore": "Bengaluru", "bengaluru": "Bengaluru",
    "hyderabad": "Hyderabad", "chennai": "Chennai", "kolkata": "Kolkata", "pune": "Pune", "ahmedabad": "Ahmedabad",
    "jaipur": "Jaipur", "lucknow": "Lucknow", "chandigarh": "Chandigarh", "indore": "Indore", "kochi": "Kochi",
    "goa": "Goa", "surat": "Surat", "kerala": "Kerala", "punjab": "Punjab", "trivandrum": "Thiruvananthapuram",
    "coimbatore": "Coimbatore", "guwahati": "Guwahati", "bhopal": "Bhopal", "nagpur": "Nagpur", "patna": "Patna",
}

_NUMBER = r"(\d+(?:\.\d+)?)\s*(k|m|lakh|lac|l)?"


def _amount(value: str, unit: Optional[str]) -> int:
    multiplier = {"k": 1_000, "m": 1_000_000, "lakh": 100_000, "lac": 100_000, "l": 100_000}.get((unit or "").lower(), 1)
    return int(float(value) * multiplier)


def parse_rules(message: str, current: Optional[Dict[str, str]] = None) -> Tuple[Dict[str, str], List[str]]:
    """Deterministic parser. Starts from the current criteria so follow-ups refine them."""
    text = message.lower()
    criteria = dict(current or {})
    notes: List[str] = []
    if re.search(r"\b(start over|reset|clear( all)?|new search)\b", text):
        criteria = {}
        notes.append("cleared previous criteria")

    between = re.search(rf"(?:between|from)\s+{_NUMBER}\s*(?:-|to|and)\s*{_NUMBER}", text) or re.search(rf"{_NUMBER}\s*(?:-|–|to)\s*{_NUMBER}\s*(?:followers?)?", text)
    if between and (between.group(2) or between.group(4)):
        criteria["min_followers"] = str(_amount(between.group(1), between.group(2) or between.group(4)))
        criteria["max_followers"] = str(_amount(between.group(3), between.group(4)))
    else:
        upper = re.search(rf"(?:under|below|less than|upto|up to|max(?:imum)?|<)\s*{_NUMBER}", text)
        lower = re.search(rf"(?:over|above|more than|at least|min(?:imum)?|>|\+)\s*{_NUMBER}", text) or re.search(rf"{_NUMBER}\s*\+", text)
        if upper:
            criteria["max_followers"] = str(_amount(upper.group(1), upper.group(2)))
        if lower:
            criteria["min_followers"] = str(_amount(lower.group(1), lower.group(2)))
    if re.search(r"\bnano\b", text):
        criteria.update(min_followers="1000", max_followers="10000")
    elif re.search(r"\bmicro\b", text) and "max_followers" not in criteria:
        criteria.update(min_followers="10000", max_followers="100000")

    for alias, city in sorted(CITY_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", text):
            criteria["city"] = city
            break
    from .niches import NICHES, primary_niche

    niche = primary_niche(text) or next(
        (name for name, (_, terms) in NICHES.items() if any(term in text for term in terms)), None)
    if niche:
        criteria["category"] = niche
    for name, code in LANGUAGES.items():
        if name in text:
            criteria["language"] = code
            break

    if re.search(r"brand (deal|collab|partnership)|sponsor|paid partnership|done (ads|collabs)|worked with brands|ads? experience", text):
        criteria["brand"] = "1"
    if re.search(r"\b(active|recent(ly)?|posting (regularly|recently)|this month|last 30 days)\b", text):
        criteria["active_days"] = "30"
    if re.search(r"\b(indian|india|desi)\b", text):
        criteria["india"] = "1"
    engagement = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:\+|or more|engagement)?", text)
    if engagement:
        criteria["min_engagement"] = str(float(engagement.group(1)) / 100)
    elif re.search(r"high(ly)? engag|good engagement|engaged audience", text):
        criteria["min_engagement"] = "0.03"

    # Words that describe a niche more precisely than a category ("biryani", "trekking").
    niche = re.search(r"(?:posts? about|posting about|talk(?:s|ing)? about|about|into|who make|who do|niche(?: is)?)\s+([a-z][a-z ]{2,30}?)(?:\s+(?:in|from|with|under|over|who|and|creators?|content)\b|[,.]|$)", text)
    if niche:
        criteria["q"] = niche.group(1).strip()
    return {k: v for k, v in criteria.items() if k in CRITERIA_KEYS and v not in (None, "")}, notes


PROMPT = """You turn a brand's request for Instagram creators in India into search criteria.
Current criteria (refine these unless the user starts over): {current}
User message: {message}

Reply with ONLY a JSON object using any of these keys (omit what the user did not ask for):
  q: niche keywords to match in bio/captions/hashtags (string)
  city: one of {cities}
  category: one of {categories}
  language: one of {languages}
  min_followers, max_followers: integers
  min_engagement: fraction, e.g. 0.03 for 3%
  india, brand: "1" when required (brand = has done brand deals)
  active_days: integer, e.g. 30 for posted in the last month
  reply: one short sentence telling the user what you are searching for
"""


SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "q": {"type": ["string", "null"]},
        "city": {"type": ["string", "null"]},
        "category": {"type": ["string", "null"]},
        "language": {"type": ["string", "null"]},
        "min_followers": {"type": ["integer", "null"]},
        "max_followers": {"type": ["integer", "null"]},
        "min_engagement": {"type": ["number", "null"]},
        "india": {"type": ["string", "null"]},
        "brand": {"type": ["string", "null"]},
        "active_days": {"type": ["integer", "null"]},
        "reply": {"type": "string"},
    },
    "required": ["reply"],
}


def parse_llm(message: str, current: Dict[str, str]) -> Optional[Tuple[Dict[str, str], str]]:
    from . import llm

    if not llm.brand_ai_enabled():
        return None
    prompt = PROMPT.format(
        current=json.dumps(current), message=message, cities=sorted(set(CITY_ALIASES.values())),
        categories=list(__import__("microindia_scraper.niches", fromlist=["NICHES"]).NICHES), languages=list(LANGUAGES.values()),
    )
    try:
        answer, _ = llm.call(prompt, schema=SEARCH_SCHEMA, model="sonnet", purpose="search.parse",
                             timeout=float(os.environ.get("MICROINDIA_LLM_TIMEOUT", "45")),
                             retries=0, slot_wait=5)  # a brand request never queues behind the analyzer
    except llm.LLMError:
        return None
    if not isinstance(answer, dict):
        return None
    answer = dict(answer)
    reply = str(answer.pop("reply", "") or "")
    criteria = {k: str(v) for k, v in answer.items() if k in CRITERIA_KEYS and v not in (None, "", False)}
    return criteria, reply


def describe(criteria: Dict[str, str]) -> str:
    from .format_helpers import compact

    parts = []
    if criteria.get("category"):
        parts.append(f"{criteria['category']} creators")
    else:
        parts.append("creators")
    if criteria.get("city"):
        parts.append(f"in {criteria['city']}")
    lo, hi = criteria.get("min_followers"), criteria.get("max_followers")
    if lo or hi:
        parts.append(f"with {compact(int(lo or 0))}–{compact(int(hi)) if hi else 'any'} followers")
    if criteria.get("q"):
        parts.append(f"about “{criteria['q']}”")
    if criteria.get("language"):
        name = next((n for n, c in LANGUAGES.items() if c == criteria["language"]), criteria["language"])
        parts.append(f"posting in {name.title()}")
    extras = []
    if criteria.get("brand"):
        extras.append("have done brand deals")
    if criteria.get("active_days"):
        extras.append(f"posted in the last {criteria['active_days']} days")
    if criteria.get("min_engagement"):
        extras.append(f"have {float(criteria['min_engagement']) * 100:.0f}%+ engagement")
    if criteria.get("india"):
        extras.append("show India evidence")
    sentence = "Searching " + " ".join(parts)
    if extras:
        sentence += " who " + ", ".join(extras)
    return sentence + "."


def chat_search(message: str, current: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    current = {k: v for k, v in (current or {}).items() if k in CRITERIA_KEYS and v}
    llm = parse_llm(message, current)
    if llm is not None:
        criteria, reply = llm
        return {"criteria": criteria, "reply": reply or describe(criteria), "engine": "llm"}
    criteria, notes = parse_rules(message, current)
    return {"criteria": criteria, "reply": describe(criteria), "engine": "rules", "notes": notes}
