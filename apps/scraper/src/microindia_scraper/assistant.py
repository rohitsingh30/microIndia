"""The brand-facing creator assistant behind the Find page.

Each message:
1. retrieve: rank in-scope creators by relevance to the brief (bio, captions,
   hashtags, city, niche) blended with engagement and brand experience, inside the user's filters and any
   hard facts the brief states ("under 50K", "in Pune");
2. answer: an LLM reads the brief, the conversation and compact dossiers of the
   top candidates, then recommends creators with a reason each and answers the
   question. Configured with MICROINDIA_LLM_CMD (e.g. an `opencode run ...`
   command reading the prompt on stdin and printing JSON).
Without an LLM it returns the retrieval ranking and says the AI is not connected.
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import subprocess
from collections import Counter
from typing import Any, Dict, List, Optional

from .insights import band_of
from .search_ai import parse_rules

STOPWORDS = set("""a an and are as at be but by for from has have i in is it me my need of on or our show the their them
they this to us we who with want looking find creators creator influencers influencer instagram some any all
that which can could would should please like more less than about into over under around get give list
new launch launching launched brand brands campaign product our your help best good great top"""
.split())
CANDIDATES = 25

SYSTEM = """You are a creator-marketing strategist helping an Indian brand pick Instagram creators.
You only know the creators listed under CANDIDATES (real data we captured). Never invent creators or numbers.
Recommend the best fits for the brief, explain each pick in one concrete sentence using the data
(engagement vs peers, brand-deal experience, niche, city, posting activity), flag real concerns,
and answer the user's question directly. If nothing fits well, say so and suggest how to widen the brief.

Reply with ONLY JSON:
{"answer": "<2-5 sentences, plain language>",
 "picks": [{"handle": "<handle from CANDIDATES>", "why": "<one sentence>"}],
 "follow_ups": ["<short suggested next question>", "..."]}
Pick at most 8, best first."""


def _terms(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-zऀ-ൿ]{3,}", text.lower()) if t not in STOPWORDS]


def retrieve(creators: List[Dict[str, Any]], message: str, history: List[Dict[str, str]],
             filters: Dict[str, str]) -> List[Dict[str, Any]]:
    """In-scope creators ranked by relevance to the brief, inside filters and stated hard facts."""
    pool = [c for c in creators if c.get("eligible") and not c.get("excluded_reason")]
    previous = " ".join(turn.get("text", "") for turn in history if turn.get("role") == "user")
    stated, _ = parse_rules(message, parse_rules(previous)[0] if previous else {})
    hard = {**{k: v for k, v in stated.items() if k in ("city", "min_followers", "max_followers", "language", "brand", "active_days")}, **filters}
    pool = [c for c in pool if _passes(c, hard)]
    terms = _terms(previous + " " + message)
    documents = len(pool) or 1
    df = Counter(term for c in pool for term in set(terms) if term in c.get("search_text", ""))
    scored = []
    for creator in pool:
        text = creator.get("search_text", "")
        relevance = sum(
            math.log(1 + documents / (1 + df[term])) * min(3, text.count(term))
            for term in set(terms) if term in text
        )
        matched = [term for term in dict.fromkeys(terms) if term in text]
        quality = min(1.0, (creator.get("engagement_rate") or 0) / 0.06) * 10 + (5 if creator.get("brand_ready") else 0)
        scored.append((relevance * 10 + quality, creator, matched))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [{**creator, "matched": matched} for _, creator, matched in scored]


def _passes(creator: Dict[str, Any], hard: Dict[str, str]) -> bool:
    followers = creator.get("followers") or 0
    if hard.get("min_followers") and followers < float(hard["min_followers"]):
        return False
    if hard.get("max_followers") and followers > float(hard["max_followers"]):
        return False
    if hard.get("city") and creator.get("city") != hard["city"]:
        return False
    if hard.get("language") and hard["language"] not in (creator.get("languages") or []):
        return False
    if hard.get("brand") and not creator.get("brand_ready"):
        return False
    if hard.get("category") and creator.get("category") != hard["category"]:
        return False
    if hard.get("min_engagement") and (creator.get("engagement_rate") or 0) < float(hard["min_engagement"]):
        return False
    if hard.get("active_days"):
        import time
        if (creator.get("last_post_ts") or 0) < time.time() - float(hard["active_days"]) * 86400:
            return False
    return True


def dossier(creator: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "handle": creator["handle"],
        "name": creator.get("name"),
        "followers": creator.get("followers"),
        "size": band_of(creator.get("followers")),
        "engagement_rate_pct": round((creator.get("engagement_rate") or 0) * 100, 2) if creator.get("engagement_rate") is not None else None,
        "city": creator.get("city"),
        "niche": creator.get("category"),
        "languages": creator.get("languages"),
        "brand_posts": creator.get("brand_posts"),
        "paid_partnerships": creator.get("paid_partnerships"),
        "open_to_collabs": creator.get("open_to_collabs"),
        "days_since_last_post": round((__import__("time").time() - creator["last_post_ts"]) / 86400) if creator.get("last_post_ts") else None,
        "bio": (creator.get("bio") or "")[:240],
        "top_hashtags": creator.get("top_hashtags", [])[:6],
    }


def _llm(prompt: str) -> Optional[Dict[str, Any]]:
    command = os.environ.get("MICROINDIA_LLM_CMD", "").strip()
    if not command:
        return None
    try:
        # "{prompt}" in the command passes the prompt as an argument (opencode run); otherwise stdin.
        argv = [part.replace("{prompt}", prompt) for part in shlex.split(command)]
        completed = subprocess.run(argv, input=None if "{prompt}" in command else prompt, capture_output=True, text=True,
                                   timeout=float(os.environ.get("MICROINDIA_LLM_TIMEOUT", "90")))
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"\{.*\}", completed.stdout, re.S)
    if completed.returncode != 0 or not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def answer(repo: Any, message: str, history: List[Dict[str, str]], filters: Dict[str, str]) -> Dict[str, Any]:
    ranked = retrieve(repo.creators(), message, history, filters)
    candidates = ranked[:CANDIDATES]
    by_handle = {c["handle"]: c for c in candidates}
    prompt = (
        f"{SYSTEM}\n\nCONVERSATION SO FAR:\n"
        + "\n".join(f"{t.get('role')}: {t.get('text')}" for t in history[-8:])
        + f"\n\nUSER: {message}\n\nCANDIDATES ({len(candidates)} of {len(ranked)} matching creators):\n"
        + "\n".join(json.dumps(dossier(c), ensure_ascii=False) for c in candidates)
    )
    reply = _llm(prompt) if candidates else None
    if candidates and os.environ.get("MICROINDIA_LLM_CMD"):
        from .runtime.tasks import TaskStore

        usage = TaskStore(repo.database)
        try:
            usage.count_usage("llm_calls")
            usage.count_usage("llm_prompt_chars", len(prompt))
        finally:
            usage.close()
    if reply and isinstance(reply.get("picks"), list):
        picks = [p for p in reply["picks"] if isinstance(p, dict) and p.get("handle") in by_handle][:8]
        engine, text, follow_ups = "llm", str(reply.get("answer") or ""), [str(f) for f in reply.get("follow_ups") or []][:3]
    else:
        picks = [{"handle": c["handle"], "why": _why(c)} for c in candidates[:8]]
        engine = "retrieval"
        text = (f"Here are the {len(picks)} closest matches out of {len(ranked)} creators, ranked by relevance to your brief, engagement and brand experience."
                if picks else "No captured creator matches that yet. Try widening the size range or city.")
        follow_ups = ["Only creators who have done brand deals", "Who posts most often?", "Show smaller creators"]
    return {
        "answer": text,
        "engine": engine,
        "matches": len(ranked),
        "picks": [{**p, "creator": repo.strip(by_handle[p["handle"]])} for p in picks],
        "follow_ups": follow_ups,
    }


def _why(creator: Dict[str, Any]) -> str:
    bits = []
    if creator.get("matched"):
        bits.append("talks about " + ", ".join(creator["matched"][:3]))
    if creator.get("followers"):
        bits.append(f"{band_of(creator['followers'])} followers")
    if creator.get("engagement_rate"):
        bits.append(f"{creator['engagement_rate'] * 100:.1f}% engagement")
    if creator.get("brand_posts"):
        bits.append(f"{creator['brand_posts']} brand post(s)")
    return "; ".join(bits).capitalize() or "Close match to your brief."
