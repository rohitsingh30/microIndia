"""End-to-end local Chrome profile collector.

This uses Browser Use's local browser mode and writes every observation to
SQLite immediately. It intentionally does not use Browser Use Cloud.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .constants import BAND_LABEL, CONTENT_CAPTURE_LIMIT, SCHEMA_VERSION, SCRAPE_MAX_FOLLOWERS, SCRAPE_MIN_FOLLOWERS
from .candidate_source import canonical_profile_url as strict_canonical_profile_url
from .eligibility import human_indian_micro_creator_reasons
from .metrics import calculate_metrics
from .models import CaptureStatus, ContentObservation, ProfileCapture, ProfileObservation
from .intelligence import extract_ai_evidence
from .store import CaptureStore

CHROME_EXECUTABLE = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DEFAULT_USER_DATA_DIR = "/Users/rohit/playwright-chrome-profile-4"
DEFAULT_PROFILE_DIRECTORY = "Default"
INSTAGRAM_BASE = "https://www.instagram.com"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_count(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    text = value.strip().lower().replace(",", "")
    match = re.search(r"([\d.]+)\s*([km]?)", text)
    if not match:
        return None
    number = float(match.group(1))
    multiplier = {"": 1, "k": 1_000, "m": 1_000_000}[match.group(2)]
    return int(number * multiplier)


def normalize_profile_url(url: str) -> str:
    try:
        canonical, _ = strict_canonical_profile_url(url)
    except ValueError as exc:
        raise ValueError("capture requires a valid Instagram profile_url") from exc
    return canonical



def _language_signals(text: str) -> List[str]:
    signals = []
    if re.search(r"[\u0900-\u097f]", text):
        signals.append("hi")
    if re.search(r"[\u0b80-\u0bff]", text):
        signals.append("ta")
    if re.search(r"[\u0c00-\u0c7f]", text):
        signals.append("te")
    if re.search(r"[\u0d00-\u0d7f]", text):
        signals.append("ml")
    if re.search(r"[A-Za-z]", text):
        signals.append("en")
    return sorted(set(signals))


def _location_signal(text: str) -> Optional[str]:
    match = re.search(r"(?:📍|location\s*[:\-])\s*([^|•\n]+)", text, re.I)
    if match:
        return match.group(1).strip()[:160]
    india_terms = (
        "india", "delhi", "mumbai", "bangalore", "bengaluru", "chennai",
        "hyderabad", "kolkata", "pune", "jaipur", "kerala", "gujarat",
        "punjab", "rajasthan", "maharashtra", "karnataka", "tamil nadu",
    )
    for line in (part.strip() for part in text.splitlines()):
        if line and any(term in line.lower() for term in india_terms):
            return line[:160]
    return None


def _business_category(text: str) -> Optional[str]:
    categories = (
        "food", "fashion", "beauty", "fitness", "travel", "technology",
        "finance", "parenting", "photography", "art", "education",
        "skincare", "makeup", "yoga", "running", "wellness", "coding",
        "gadget", "startup", "investing", "money", "book",
    )
    lowered = text.lower()
    return next((category for category in categories if category in lowered), None)


def _commercial_signals(text: str) -> Dict[str, bool]:
    lowered = text.lower()
    return {
        "has_business_contact_prompt": bool(re.search(r"\b(email|contact|booking|work with|for collab)\b", lowered)),
        "has_sponsorship_language": bool(re.search(r"\b(collab|sponsor|sponsored|paid partnership|brand)\b", lowered)),
        "has_call_to_action": bool(re.search(r"\b(follow|subscribe|dm|link in bio|save|share)\b", lowered)),
    }


def _caption_from_description(description: str) -> Optional[str]:
    if not description:
        return None
    # Instagram's OpenGraph description commonly prefixes the caption with
    # engagement text and a colon. Preserve the original when no prefix exists.
    match = re.search(r":\s*(.+)$", description, re.S)
    return (match.group(1) if match else description).strip() or None


# Words in Instagram's category label ("Buffet Restaurant", "Coffee shop") that mean a business.
BUSINESS_LABEL_WORDS = re.compile(
    r"restaurant|cafe|café|bakery|\bbar\b|pub|brewery|hotel|resort|store|shop|retail|brand|company|product/service|"
    r"local business|agency|advertising|marketing|media|news|school|college|university|academy|education|clinic|"
    r"hospital|salon|spa\b|boutique|caterer|catering|event planner|organization|nonprofit|community|e-commerce|"
    r"real estate|travel company|tour agency|gym/physical fitness center|food & beverage|grocery|supermarket",
    re.I,
)
_STAT_LINE = re.compile(r"^\d[\d.,]*\s*[km]?\s*(posts?|followers?|following)$", re.I)
_HEADER_SKIP = re.compile(
    r"^(follow|following|message|contact|email|call|directions|book now|order food|view shop|edit profile|"
    r"share profile|options|follow back|subscribe|see translation|more)$",
    re.I,
)


def parse_header(header: str, handle: str, display_name: Optional[str]) -> Dict[str, Any]:
    """Real bio and Instagram's category label from the profile header text.

    Layout: handle, display name, counts, [category label], bio lines, link, highlights.
    """
    lines = [line.strip() for line in header.splitlines() if line.strip()]
    stats = [i for i, line in enumerate(lines) if _STAT_LINE.match(line)]
    after = lines[stats[-1] + 1:] if stats else lines
    names = {handle.lower(), (display_name or "").lower()}
    label: Optional[str] = None
    bio_lines: List[str] = []
    for index, line in enumerate(after):
        lowered = line.lower()
        if lowered in names or _HEADER_SKIP.match(line):
            continue
        if lowered.startswith(("followed by", "threads")):
            break
        if index == 0 and label is None and re.fullmatch(r"[A-Z][A-Za-z &/,()'.-]{2,40}", line) and not re.search(r"[|📍•]", line):
            label = line  # Instagram's category label, e.g. "Digital creator", "Buffet Restaurant"
            continue
        bio_lines.append(re.sub(r"\s*\.\.\.\s*$", "", line))
        if re.search(r"\band \d+ more$", line) or re.match(r"^(https?://|www\.|[\w-]+\.(com|in|me|link|ee|bio)\b)", lowered):
            break  # the link line ends the bio; story highlights follow
    return {
        "bio": "\n".join(bio_lines).strip() or None,
        "category_label": label,
        "is_business_label": bool(label and BUSINESS_LABEL_WORDS.search(label)),
    }


def parse_profile_observation(
    data: Dict[str, Any],
    capture_id: str,
    profile_url: str,
    observed_at: str,
) -> ProfileObservation:
    description = str(data.get("description") or "")
    body = str(data.get("bodyText") or "")
    header = str(data.get("headerText") or "")
    structured = data.get("structuredProfile") or {}
    if not isinstance(structured, dict):
        structured = {}
    handle_value = profile_url.rstrip("/").split("/")[-1]
    title_name = (data.get("title") or "").split("(")[0].strip() or None
    parsed_header = parse_header(header, handle_value, structured.get("name") or title_name)
    research_text = " ".join(
        str(value) for value in (
            parsed_header["bio"],
            structured.get("description"),
            data.get("bioText"),
            description,
            header,
            body,
        ) if value
    )
    follower_text = re.search(r"([\d.,]+\s*[KM]?)\s*followers", research_text, re.I)
    following_text = re.search(r"([\d.,]+\s*[KM]?)\s*following", research_text, re.I)
    posts_text = re.search(r"([\d.,]+\s*[KM]?)\s*posts", research_text, re.I)
    bio_text = structured.get("description") or data.get("bioText") or parsed_header["bio"] or description or None
    category = _business_category(research_text)
    ai = extract_ai_evidence(research_text, profile=True)
    return ProfileObservation(
        capture_id=capture_id,
        handle=profile_url.rstrip("/").split("/")[-1],
        profile_url=profile_url,
        platform_user_id=str(data["profileId"]) if str(data.get("profileId") or "").isdigit() else None,
        display_name=structured.get("name") or (data.get("title") or "").split("(")[0].strip() or None,
        bio_text=bio_text,
        external_url=data.get("externalUrl"),
        follower_count=parse_count(follower_text.group(1) if follower_text else None),
        following_count=parse_count(following_text.group(1) if following_text else None),
        post_count=parse_count(posts_text.group(1) if posts_text else None),
        is_verified=bool(data.get("isVerified")) or "verified" in research_text.lower(),
        is_private=bool(data.get("isPrivate")) or "private account" in research_text.lower(),
        # Instagram's category label ("Restaurant", "Digital creator") is the best account-type signal.
        account_type=data.get("accountType") or ("business" if parsed_header["is_business_label"] else parsed_header["category_label"]),
        business_category=category,
        location_text=_location_signal(research_text),
        # Only the creator's own words: the page body also holds Instagram's footer,
        # which lists every UI language in its native script.
        language_signals=_language_signals(" ".join(
            str(value) for value in (structured.get("name"), structured.get("description"), data.get("bioText"), header) if value
        )),
        commercial_signals=_commercial_signals(research_text),
        ai_signals=ai["ai_signals"],
        ai_score=ai["ai_score"],
        ai_label=ai["ai_label"],
        ai_evidence=ai["ai_evidence"],
        observed_at=observed_at,
    )


def parse_content_observation(
    data: Dict[str, Any],
    capture_id: str,
    index: int,
    permalink: str,
    observed_at: str,
) -> ContentObservation:
    description = str(data.get("description") or "")
    text = str(data.get("text") or "")
    article_text = str(data.get("articleText") or "")
    caption = data.get("caption") or _caption_from_description(description)
    caption = str(caption).strip() if caption else None
    evidence = " ".join(value for value in (caption or "", text, article_text) if value)
    # The meta description ("2,345 likes, 45 comments - handle on <date>: caption")
    # is the authoritative count; the page body also holds other people's
    # comments ("1 like") that must not be mistaken for the post's metrics.
    counts_text = description.split(":", 1)[0] if re.search(r"\blikes?\b", description, re.I) else ""
    likes = re.search(r"([\d.,]+\s*[KM]?)\s*likes?", counts_text, re.I) or re.search(r"([\d.,]+\s*[KM]?)\s*likes?", evidence, re.I)
    comments = re.search(r"([\d.,]+\s*[KM]?)\s*comments?", counts_text, re.I) or re.search(r"([\d.,]+\s*[KM]?)\s*comments?", evidence, re.I)
    views = re.search(r"([\d.,]+\s*[KM]?)\s*(?:views|plays)", evidence, re.I)
    hashtags = sorted(set(re.findall(r"#[\w]+", evidence)))
    mentions = sorted(set(re.findall(r"@[\w.]+", evidence)))
    content_type = data.get("type", "unknown")
    ai = extract_ai_evidence(" ".join(value for value in (caption, text, article_text, description) if value))
    return ContentObservation(
        capture_id=capture_id,
        content_index=index,
        permalink=permalink,
        platform_content_id=permalink.rstrip("/").split("/")[-1],
        content_type=content_type,
        published_at=data.get("publishedAt"),
        is_pinned=bool(data.get("isPinned")) if data.get("isPinned") is not None else None,
        is_collaboration=bool(data.get("isCollaboration")) if data.get("isCollaboration") is not None else None,
        is_paid_partnership=bool(data.get("isPaidPartnership")) or bool(re.search(r"\bpaid partnership\b", article_text[:1500] or text[:1500], re.I)),
        caption_text=caption,
        text_content=text or None,
        hashtags=hashtags,
        mentions=mentions,
        like_count=parse_count(likes.group(1) if likes else None),
        comment_count=parse_count(comments.group(1) if comments else None),
        view_count=parse_count(views.group(1) if views else None),
        location_text=data.get("locationText") or _location_signal(evidence),
        metric_availability="full" if likes and comments else "partial" if likes or comments else "unavailable",
        ai_signals=ai["ai_signals"],
        ai_score=ai["ai_score"],
        ai_label=ai["ai_label"],
        ai_evidence=ai["ai_evidence"],
        observed_at=observed_at,
    )


def research_quality(profile: Dict[str, Any], content: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Score whether the capture contains useful structured research evidence."""
    profile_fields = (
        "handle",
        "display_name",
        "follower_count",
        "following_count",
        "post_count",
        "bio_text",
        "location_text",
        "business_category",
        "language_signals",
    )
    missing_fields = [
        field for field in profile_fields
        if profile.get(field) in (None, "", [])
    ]
    observed = [item for item in content if item.get("item_status", "observed") == "observed"]
    content_fields = ("caption_text", "published_at", "hashtags", "like_count", "comment_count")
    coverage = [
        sum(1 for item in observed if item.get(field) not in (None, "", [])) / len(observed)
        for field in content_fields
    ] if observed else []
    profile_coverage = (len(profile_fields) - len(missing_fields)) / len(profile_fields)
    content_coverage = sum(coverage) / len(coverage) if coverage else 0.0
    score = round(min(100.0, profile_coverage * 45.0 + (len(observed) / 18.0) * 35.0 + content_coverage * 20.0), 1)
    return {
        "missing_fields": missing_fields,
        "completeness_score": score,
        "observed_content_count": len(observed),
        "content_field_coverage": {
            field: round(value, 3) for field, value in zip(content_fields, coverage)
        },
    }

def quality_gate(
    profile: Dict[str, Any],
    content: List[Dict[str, Any]],
    quality: Dict[str, Any],
    eligibility_reasons: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Light gate: only the hard eligibility facts reject a creator.

    Post count, completeness, missing fields and AI evidence are reported as
    ``notes`` so they can be filtered on, but they never drop a profile.
    """
    reasons = [f"ELIGIBILITY:{reason}" for reason in (eligibility_reasons or [])]
    if profile.get("follower_count") in (None, ""):
        reasons.append("MISSING_CORE_PROFILE:follower_count")
    observed = [item for item in content if item.get("item_status", "observed") == "observed"]
    notes: List[str] = []
    if len(observed) < 8:
        notes.append(f"FEW_POSTS:{len(observed)}")
    if float(quality.get("completeness_score") or 0.0) < 65.0:
        notes.append(f"LOW_COMPLETENESS:{quality.get('completeness_score', 0.0)}")
    # AI personas are a hard no: an AI-dominant profile, or AI evidence on 3+ of the first 8 posts.
    if profile.get("ai_label") == "ai_dominant" or any(
        str(signal).startswith("profile_") for signal in profile.get("ai_signals") or []
    ):
        reasons.append("AI_PROFILE_DOMINANT")
    high_confidence_ai = sum(float(item.get("ai_score") or 0.0) >= 0.75 for item in observed[:8])
    if high_confidence_ai >= 3:
        reasons.append(f"AI_CONTENT_REPEATED:{high_confidence_ai}/8")
    return {
        "eligible": not reasons,
        "reasons": reasons,
        "notes": notes,
        "ai_label": profile.get("ai_label") or "unknown",
        "high_confidence_ai_items_first_eight": high_confidence_ai,
        "evidence_scope": "public_text_and_metadata",
    }

_CATEGORY_QUERY_ALIASES = {
    "food": {"food", "cooking", "recipe", "baking"},
    "fashion": {"fashion", "style"},
    "beauty": {"beauty", "skincare", "makeup"},
    "fitness": {"fitness", "yoga", "running", "wellness"},
    "travel": {"travel"},
    "technology": {"technology", "tech", "coding", "gadget", "startup"},
    "finance": {"finance", "investing", "money"},
    "parenting": {"parenting"},
    "photography": {"photography", "photo"},
    "art": {"art"},
    "education": {"education", "book"},
}


def _category_matches_niche(category: Optional[str], niche: Optional[str]) -> bool:
    if not category or not niche:
        return True
    query_terms = set(re.findall(r"[a-z]+", niche.lower()))
    return bool(query_terms.intersection(_CATEGORY_QUERY_ALIASES.get(category, {category})))


def content_capture_skip_reasons(
    profile: ProfileObservation,
    eligibility_reasons: Optional[List[str]] = None,
    requested_niche: Optional[str] = None,
) -> List[str]:
    """Skip deep post capture only on hard facts.

    India, category and location evidence usually lives in the posts, so a
    missing bio signal is never a reason to stop before opening them.
    ``requested_niche`` is accepted for compatibility; niche is not a gate.
    """
    reasons: List[str] = []
    if profile.is_private:
        reasons.append("private account")
    if profile.follower_count is not None and not SCRAPE_MIN_FOLLOWERS <= profile.follower_count <= SCRAPE_MAX_FOLLOWERS:
        reasons.append("outside follower range (500-1M)")
    reasons.extend(
        reason for reason in (eligibility_reasons or [])
        if reason.startswith(("publisher/", "big business", "AI-generated", "repost/"))
    )
    return sorted(set(reasons))


async def evaluate(page: Any, script: str) -> Any:
    result = await page.evaluate(script)
    if not isinstance(result, str) or not result:
        return result
    try:
        return json.loads(result)
    except json.JSONDecodeError:
        return result




def _manual_auth_evidence(data: Dict[str, Any]) -> Optional[str]:
    marker_text = "\n".join(
        str(data.get(key) or "") for key in ("url", "title", "bodyText", "headerText")
    )
    title = str(data.get("title") or "")
    body = str(data.get("bodyText") or "")
    if re.search(r"/accounts/login|/challenge|/checkpoint", marker_text, re.I):
        return "MANUAL_AUTH_RECHECK"
    if re.search(r"\b(login|challenge|checkpoint)\b", title, re.I):
        return "MANUAL_AUTH_RECHECK"
    if re.search(r"log\s*in\s+to\s+instagram|login\s+required|challenge|checkpoint", body, re.I):
        return "MANUAL_AUTH_RECHECK"
    return None


async def extract_profile(page: Any, capture_id: str, profile_url: str) -> ProfileObservation:
    await page.goto(profile_url)
    # The header renders after the document; give it up to ~6s before reading.
    for _ in range(12):
        await asyncio.sleep(0.5)
        if await evaluate(page, "() => !!document.querySelector('header') && (document.querySelector('header').innerText || '').length > 20"):
            break
    data = await evaluate(
        page,
        """() => {
          const meta = name => document.querySelector(`meta[property="${name}"], meta[name="${name}"]`)?.content || null;
          const text = document.body?.innerText || '';
          const header = document.querySelector('header');
          const structured = Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
            .map(script => { try { return JSON.parse(script.textContent || '{}'); } catch (_) { return {}; } })
            .find(item => item && (item['@type'] === 'ProfilePage' || item['@type'] === 'Person')) || {};
          const links = Array.from(document.querySelectorAll('a[href]')).map(a => ({href: a.href, text: a.innerText}));
          const external = links.find(link => !link.href.includes('instagram.com') && link.href.startsWith('http'));
          const html = document.documentElement.innerHTML;
          const idMatch = html.match(/"profile_id":"(\d+)"/) || html.match(/profilePage_(\d+)/);
          return {
            url: location.href,
            profileId: idMatch ? idMatch[1] : null,
            title: document.title,
            description: meta('og:description') || meta('description'),
            image: meta('og:image'),
            bodyText: text.slice(0, 16000),
            headerText: header?.innerText || (document.querySelector('main')?.innerText || '').slice(0, 1500),
            bioText: structured.description || null,
            structuredProfile: structured,
            isPrivate: /private account/i.test(text),
            isVerified: /verified/i.test(header?.innerText || ''),
            externalUrl: external?.href || null,
            links
          };
        }""",
    )
    if not isinstance(data, dict):
        raise RuntimeError("Instagram profile page did not return structured data")
    profile = parse_profile_observation(data, capture_id, profile_url, now())
    auth_evidence = _manual_auth_evidence(data)
    if auth_evidence:
        profile.ai_evidence = sorted(set(profile.ai_evidence + [auth_evidence]))
    return profile


async def extract_content_links(page: Any) -> List[str]:
    for _ in range(4):
        await evaluate(page, "() => { window.scrollBy(0, window.innerHeight * 1.5); return true; }")
        await asyncio.sleep(1)
    data = await evaluate(
        page,
        r"""() => Array.from(document.querySelectorAll('a[href]'))
          .map(a => a.href)
          .filter(href => href.includes('/p/') || href.includes('/reel/') || href.includes('/tv/'))""",
    )
    links = []
    for link in data or []:
        clean = link.split("?")[0].rstrip("/") + "/"
        if clean not in links:
            links.append(clean)
        if len(links) >= CONTENT_CAPTURE_LIMIT:
            break
    return links


async def extract_content_item(page: Any, capture_id: str, index: int, permalink: str) -> ContentObservation:
    await page.goto(permalink)
    await asyncio.sleep(1)
    data = await evaluate(
        page,
        """() => {
          const meta = name => document.querySelector(`meta[property="${name}"], meta[name="${name}"]`)?.content || null;
          const article = document.querySelector('article');
          const text = document.body?.innerText || '';
          const articleText = article?.innerText || '';
          const locationLink = Array.from(document.querySelectorAll('a[href]'))
            .find(a => a.href.includes('/explore/locations/'));
          const captionNode = article?.querySelector('h1');
          const time = document.querySelector('time[datetime]');
          return {
            url: location.href,
            description: meta('og:description') || meta('description') || '',
            title: document.title,
            text: text.slice(0, 9000),
            articleText: articleText.slice(0, 9000),
            caption: captionNode?.innerText || null,
            publishedAt: time?.getAttribute('datetime') || null,
            locationText: locationLink?.innerText || null,
            isPinned: /\\bpinned\\b/i.test(articleText),
            isCollaboration: /\\b(collab|collaboration|with @|invite collaborator)\\b/i.test(articleText),
            isPaidPartnership: /paid partnership/i.test((document.querySelector('header')?.innerText || '') + ' ' + articleText.slice(0, 1500)),
            type: location.pathname.includes('/reel/') ? 'reel' : location.pathname.includes('/tv/') ? 'video' : 'post'
          };
        }""",
    )
    if not isinstance(data, dict):
        raise RuntimeError("Instagram content page did not return structured data")
    return parse_content_observation(data, capture_id, index, permalink, now())


async def _select_page(browser: Any, page_target_id: Optional[str]) -> Any:
    pages = await browser.get_pages()
    if page_target_id:
        for page in pages:
            if getattr(page, "target_id", None) == page_target_id:
                return page
        raise RuntimeError(f"Shared browser page target was not found: {page_target_id}")
    page = await browser.get_current_page()
    if page is not None:
        return page
    return await browser.new_page()


async def run(
    profile_url: Optional[str],
    user_data_dir: Optional[str],
    profile_directory: str,
    database_path: str,
    *,
    cdp_url: Optional[str] = None,
    page_target_id: Optional[str] = None,
    browser_session: Any = None,
    page: Any = None,
    requested_niche: Optional[str] = None,
) -> Dict[str, Any]:
    if not profile_url:
        raise ValueError("capture requires an explicit profile_url")
    target_url = normalize_profile_url(profile_url)
    if not target_url or target_url == f"{INSTAGRAM_BASE}//":
        raise ValueError("capture requires a valid profile_url")
    owns_browser = browser_session is None
    if owns_browser:
        from browser_use import Browser  # only needed when this run owns the browser
    if browser_session is not None:
        browser = browser_session
    elif cdp_url:
        browser = Browser(cdp_url=cdp_url)
    else:
        if not user_data_dir:
            raise ValueError("Local browser mode requires user_data_dir")
        browser = Browser(
            executable_path=CHROME_EXECUTABLE,
            user_data_dir=user_data_dir,
            profile_directory=profile_directory,
            headless=False,
        )
    store = CaptureStore(database_path)
    capture_id = str(uuid.uuid4())
    try:
        if owns_browser:
            await browser.start()
        if page is None:
            page = await _select_page(browser, page_target_id)
        handle = target_url.rstrip("/").split("/")[-1]
        capture = ProfileCapture(
            capture_id=capture_id,
            candidate_key=f"instagram:{handle}",
            profile_url=target_url,
            captured_at=now(),
            schema_version=SCHEMA_VERSION,
            requested_content_count=CONTENT_CAPTURE_LIMIT,
        )
        store.create_capture(capture)
        profile = await extract_profile(page, capture_id, target_url)
        store.save_profile_snapshot(profile, profile.observed_at or now())
        auth_recheck = "MANUAL_AUTH_RECHECK" in profile.ai_evidence
        if profile.is_private is True or auth_recheck:
            evidence = "PRIVATE_PROFILE_RECHECK" if profile.is_private is True else "MANUAL_AUTH_RECHECK"
            store.update_progress(
                capture_id,
                observed_content_count=0,
                last_completed_index=0,
                status=CaptureStatus.QUARANTINED,
                warnings=[evidence],
            )
            return {
                "capture_id": capture_id,
                "profile_url": target_url,
                "status": CaptureStatus.QUARANTINED.value,
                "evidence": evidence,
            }
        eligibility_reasons = human_indian_micro_creator_reasons(profile)
        content_skip_reasons = content_capture_skip_reasons(
            profile,
            eligibility_reasons,
            requested_niche,
        )
        content_links = [] if content_skip_reasons else await extract_content_links(page)
        for index, permalink in enumerate(content_links, start=1):
            if index in store.get_saved_content_indexes(capture_id):
                continue
            try:
                item = await extract_content_item(page, capture_id, index, permalink)
                store.save_content_snapshot(item, item.observed_at or now())
                store.update_progress(capture_id, observed_content_count=index, last_completed_index=index)
            except Exception as exc:
                if type(exc).__name__ == "Throttled":
                    raise  # stop this capture; the task is retried after the shared cooldown
                store.update_progress(
                    capture_id,
                    observed_content_count=index - 1,
                    last_completed_index=index - 1,
                    status=CaptureStatus.PARTIAL,
                    warnings=[f"CONTENT_{index}_FAILED: {type(exc).__name__}"],
                )
        profile_payload = store.get_profile_payload(capture_id) or {}
        content_payloads = store.get_content_payloads(capture_id)
        # Re-judge with the posts in hand: captions, locations and hashtags carry the India evidence.
        content_text = " ".join(
            " ".join(str(item.get(key) or "") for key in ("caption_text", "location_text"))
            + " " + " ".join(item.get("hashtags") or [])
            for item in content_payloads
        )
        eligibility_reasons = human_indian_micro_creator_reasons(profile, content_text)
        metrics = calculate_metrics(profile_payload, content_payloads)
        store.save_metrics(capture_id, metrics, now())
        store.materialize_capture_features(capture_id, SCHEMA_VERSION, now())
        quality = research_quality(profile_payload, content_payloads)
        gate = quality_gate(profile_payload, content_payloads, quality, eligibility_reasons)
        final_status = (
            CaptureStatus.QUARANTINED
            if not gate["eligible"]
            else CaptureStatus.COMPLETE if len(content_payloads) >= 8 else CaptureStatus.PARTIAL
        )
        existing_capture = store.get_capture(capture_id) or {}
        quality_warnings = [f"RESEARCH_MISSING:{field}" for field in quality["missing_fields"]]
        gate_warnings = [f"QUALITY_GATE:{reason}" for reason in gate["reasons"]]
        content_warnings = [f"CONTENT_SKIPPED_PRECAPTURE:{reason}" for reason in content_skip_reasons]
        store.update_progress(
            capture_id,
            observed_content_count=len(content_payloads),
            last_completed_index=len(content_payloads),
            status=final_status,
            missing_fields=quality["missing_fields"],
            warnings=(
                (existing_capture.get("warnings") or [])
                + quality_warnings
                + gate_warnings
                + content_warnings
            ),
            completeness_score=quality["completeness_score"],
        )
        return {
            "capture_id": capture_id,
            "profile_url": target_url,
            "status": final_status.value,
            "metrics": metrics,
            "research_quality": quality,
            "quality_gate": gate,
        }
    except Exception as exc:
        if store.get_capture(capture_id):
            saved_indexes = store.get_saved_content_indexes(capture_id)
            existing_capture = store.get_capture(capture_id) or {}
            manual_auth = any(term in str(exc).lower() for term in ("authentication", "login", "challenge", "verification"))
            evidence = "MANUAL_AUTH_RECHECK" if manual_auth else f"{type(exc).__name__}: {exc}"
            store.update_progress(
                capture_id,
                observed_content_count=len(saved_indexes),
                last_completed_index=max(saved_indexes, default=0),
                status=CaptureStatus.NEEDS_MANUAL_AUTH if manual_auth else CaptureStatus.FAILED,
                warnings=(existing_capture.get("warnings") or []) + [evidence],
            )
        raise
    finally:
        store.close()
        if owns_browser:
            try:
                await browser.stop()
            except Exception:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one local Browser Use Instagram profile capture")
    parser.add_argument("profile_url", nargs="?", help="Optional public Instagram profile URL")
    parser.add_argument("--user-data-dir", default=os.environ.get("MICROINDIA_CHROME_DATA_DIR", DEFAULT_USER_DATA_DIR))
    parser.add_argument("--profile-directory", default=os.environ.get("MICROINDIA_CHROME_PROFILE", DEFAULT_PROFILE_DIRECTORY))
    parser.add_argument("--cdp-url", default=os.environ.get("MICROINDIA_CDP_URL"), help="Attach to one already-running shared Chrome process")
    parser.add_argument("--page-target-id", default=os.environ.get("MICROINDIA_PAGE_TARGET_ID"), help="Use this tab target when attached over CDP")
    parser.add_argument("--database", default=os.environ.get("MICROINDIA_DATABASE", "data/microindia.sqlite3"))
    args = parser.parse_args()
    try:
        result = asyncio.run(
            run(
                args.profile_url,
                args.user_data_dir,
                args.profile_directory,
                args.database,
                cdp_url=args.cdp_url,
                page_target_id=args.page_target_id,
            )
        )
        print(json.dumps(result, indent=2, sort_keys=True))
    except Exception as exc:
        print(f"E2E capture failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()