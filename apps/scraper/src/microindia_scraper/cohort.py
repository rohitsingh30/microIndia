"""Discovery and progress helpers for the India human-creator cohort."""

import json
import random
import re
import time
from datetime import datetime
from urllib.parse import urlparse
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .eligibility import is_human_indian_micro_creator
from .models import ProfileObservation
from .constants import COHORT_MAX_FOLLOWERS, COHORT_MIN_FOLLOWERS
from .store import CaptureStore


# Broad, intentionally mixed discovery buckets. The live profile is always
# rechecked; a bucket is not evidence that an account is eligible.
DISCOVERY_BUCKETS: Sequence[str] = (
    "delhi food blogger", "mumbai food blogger", "bangalore food blogger", "chennai food blogger",
    "hyderabad food blogger", "kolkata food blogger", "pune food blogger", "jaipur food blogger",
    "kerala food creator", "gujarati food creator", "punjabi food creator", "marathi food creator",
    "hindi fashion creator", "tamil fashion creator", "telugu fashion creator", "malayalam fashion creator",
    "indian beauty creator", "indian skincare creator", "delhi makeup creator", "mumbai makeup creator",
    "indian fitness creator", "indian yoga creator", "indian running creator", "indian wellness creator",
    "indian travel creator", "kerala travel creator", "rajasthan travel creator", "northeast india travel creator",
    "indian tech creator", "indian coding creator", "indian gadget creator",
    "indian finance creator", "indian investing creator", "indian money creator", "indian startup creator",
    "indian parenting creator", "indian book creator", "indian photography creator", "indian art creator",
)
DISCOVERY_SLOT_COUNT = 10
RESERVED_PROFILE_ROUTES = {
    "about", "accounts", "developer", "direct", "directory", "emails", "explore", "feed",
    "for_you", "hashtag", "home", "legal", "live", "locations", "oauth", "p", "press",
    "privacy", "push", "reel", "reels", "stories", "tags", "terms", "tv", "web",
}



def _profile_url(username: str) -> str:
    handle = re.sub(r"[^A-Za-z0-9._]", "", str(username)).strip(".").lower()
    if not handle or handle in RESERVED_PROFILE_ROUTES:
        return ""
    return f"https://www.instagram.com/{handle}/"


def canonical_profile_url(value: Any) -> str:
    """Return one canonical public profile URL, or an empty string."""
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if not candidate:
        return ""
    if "://" not in candidate:
        return _profile_url(candidate)
    parsed = urlparse(candidate)
    if parsed.scheme.lower() != "https" or parsed.netloc.lower() not in {"instagram.com", "www.instagram.com"}:
        return ""
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 1 or parts[0].lower() in RESERVED_PROFILE_ROUTES:
        return ""
    return _profile_url(parts[0])
def _for_you_profile_url(value: Any) -> str:
    url = canonical_profile_url(value)
    if url or not isinstance(value, str):
        return url
    parsed = urlparse(value.strip())
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme.lower() == "https"
        and parsed.netloc.lower() in {"instagram.com", "www.instagram.com"}
        and len(parts) == 2
        and parts[1].lower() == "reels"
    ):
        return canonical_profile_url(f"https://www.instagram.com/{parts[0]}/")
    return ""


async def discover_for_you_profile_urls(page: Any, limit: int = 100) -> List[str]:
    """Extract visible Instagram author profiles from the For You/Reels surface."""
    if limit < 1:
        return []
    result = await page.evaluate(
        f"""(...args) => (async () => {{
          const collect = () => {{
            window.scrollBy(0, Math.max(window.innerHeight || 800, 800));
            const urls = [];
            const seen = new Set();
            for (const link of document.querySelectorAll('a[href]')) {{
              const raw = link.href || link.getAttribute('href') || '';
              if (!raw || seen.has(raw)) continue;
              seen.add(raw);
              urls.push(raw);
              if (urls.length >= {limit * 3}) break;
            }}
            return urls;
          }};
          for (let attempt = 0; attempt < 20; attempt++) {{
            const urls = collect();
            if (urls.length) return urls;
            await new Promise(resolve => setTimeout(resolve, 250));
          }}
          return collect();
        }})()""",
    )
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return []
    if not isinstance(result, list):
        return []
    urls = []
    seen = set()
    for value in result:
        url = _for_you_profile_url(value)
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
            if len(urls) >= limit:
                break
    return urls

def _is_private_discovery_item(item: Any) -> bool:
    """Reject candidates whose discovery payload explicitly marks them private."""
    if not isinstance(item, dict):
        return False
    nested = item.get("user")
    if isinstance(nested, dict):
        item = {**nested, **item}
    for key in ("is_private", "isPrivate", "is_private_account", "private"):
        value = item.get(key)
        if value is True or (isinstance(value, (int, float)) and value == 1):
            return True
        if isinstance(value, str) and value.strip().lower() in {"true", "yes", "private"}:
            return True
    marker_text = " ".join(
        str(item.get(key) or "")
        for key in ("full_name", "fullName", "name", "bio", "biography", "text")
    )
    return bool(re.search(r"\bprivate\s+account\b|\bthis\s+account\s+is\s+private\b", marker_text, re.I))


_GENERIC_QUERY_WORDS = {
    "an", "and", "creator", "blogger", "in", "indian", "india", "the", "with",
}
_NON_PERSON_ACCOUNT_TYPES = {"media", "organization", "organisation", "news", "channel"}
_CATEGORY_FIELDS = ("category", "category_name", "categoryName", "biography", "bio")
_FOLLOWER_FIELDS = ("follower_count", "followers", "followers_count", "followerCount")


def _candidate_count(item: Dict[str, Any]) -> Optional[int]:
    values = [item.get(key) for key in _FOLLOWER_FIELDS]
    followed_by = item.get("edge_followed_by")
    if isinstance(followed_by, dict):
        values.append(followed_by.get("count"))
    for value in values:
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            return int(value)
        match = re.fullmatch(r"\s*([\d,.]+)\s*([kKmM]?)\s*", str(value))
        if match:
            number = float(match.group(1).replace(",", ""))
            return int(number * {"": 1, "k": 1_000, "m": 1_000_000}[match.group(2).lower()])
    return None


def _has_explicit_public_flag(item: Dict[str, Any]) -> bool:
    for key in ("is_private", "isPrivate", "is_private_account", "private"):
        if key not in item:
            continue
        value = item[key]
        if value is False or (isinstance(value, (int, float)) and value == 0):
            return True
        if isinstance(value, str) and value.strip().lower() in {"false", "no", "public"}:
            return True
        return False
    return False


def _is_potential_profile_item(item: Any, query: str) -> bool:
    """Require explicit public evidence and reject known range/category mismatches."""
    if not isinstance(item, dict):
        return False
    nested = item.get("user")
    if isinstance(nested, dict):
        item = {**nested, **item}
    if not _has_explicit_public_flag(item) or _is_private_discovery_item(item):
        return False
    account_type = str(item.get("account_type") or item.get("accountType") or "").strip().lower()
    if account_type in _NON_PERSON_ACCOUNT_TYPES:
        return False
    if item.get("is_business") is True or item.get("is_organization") is True:
        return False
    followers = _candidate_count(item)
    if followers is not None and not COHORT_MIN_FOLLOWERS <= followers <= COHORT_MAX_FOLLOWERS:
        return False
    query_terms = {
        term for term in re.findall(r"[a-z]+", query.lower())
        if term not in _GENERIC_QUERY_WORDS
    }
    category_text = " ".join(str(item.get(key) or "") for key in _CATEGORY_FIELDS).lower()
    if category_text and query_terms and not query_terms.intersection(set(re.findall(r"[a-z]+", category_text))):
        return False
    return True



async def discover_profile_urls(page: Any, query: str) -> List[str]:
    """Find explicitly public users; profile preflight verifies missing range/category metadata."""
    query_literal = json.dumps(query)
    result = await page.evaluate(
        f"""(...args) => fetch('https://www.instagram.com/web/search/topsearch/?query=' + encodeURIComponent({query_literal}), {{
          credentials: 'include',
          headers: {{'X-Requested-With': 'XMLHttpRequest'}}
        }}).then(async response => {{
          const body = await response.text();
          try {{
            const data = JSON.parse(body);
            return {{status: response.status, final_url: response.url, users: (data.users || []).map(item => item.user || item)}};
          }} catch (_) {{
            return {{status: response.status, final_url: response.url, users: [], html: body.slice(0, 2000)}};
          }}
        }})""",
    )
    for _ in range(2):
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError:
                result = {}
        else:
            break
    if not isinstance(result, dict):
        return []
    users = result.get("users") or []
    if isinstance(users, str):
        try:
            users = json.loads(users)
        except json.JSONDecodeError:
            users = []
    if isinstance(users, list) and users:
        urls = []
        for item in users:
            if not _is_potential_profile_item(item, query):
                continue
            if isinstance(item, str):
                candidate = canonical_profile_url(item)
            elif isinstance(item, dict):
                candidate = canonical_profile_url(item.get("username") or item.get("url") or "")
            else:
                candidate = ""
            if candidate:
                urls.append(candidate)
        return sorted(set(urls))
    final_url = str(result.get("final_url") or "").lower()
    html = str(result.get("html") or "").lower()
    auth_markers = ("/accounts/login", "/challenge", "/checkpoint", "log in to instagram", "log in • instagram")
    if any(marker in final_url or marker in html for marker in auth_markers):
        raise RuntimeError("Instagram requires manual authentication in the local Chrome profile")
    return []


def deterministic_slot_queries(selection_seed: int, round_index: int) -> List[Dict[str, Any]]:
    """Select one reproducible niche/query for each logical collection slot."""
    return [
        {
            "agent_slot": slot,
            "niche": random.Random(selection_seed + round_index * DISCOVERY_SLOT_COUNT + slot).choice(DISCOVERY_BUCKETS),
            "selection_seed": selection_seed,
            "round_index": round_index,
        }
        for slot in range(DISCOVERY_SLOT_COUNT)
    ]


def eligible_profile_count(store: CaptureStore) -> int:
    """Count only persisted snapshots that pass the human India micro policy."""

    rows = store.connection.execute(
        """SELECT p.payload FROM profile_snapshots p
        JOIN profile_captures c ON c.capture_id = p.capture_id
        WHERE c.status IN ('complete', 'partial')"""
    ).fetchall()
    handles = set()
    for row in rows:
        try:
            profile = ProfileObservation(**json.loads(row["payload"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if is_human_indian_micro_creator(profile):
            handle = (profile.handle or profile.profile_url or "").lower().rstrip("/").split("/")[-1]
            if handle:
                handles.add(handle)
    return len(handles)


def queue_depth(store: CaptureStore) -> int:
    row = store.connection.execute(
        "SELECT COUNT(*) AS count FROM collection_jobs WHERE status IN ('queued', 'leased')"
    ).fetchone()
    return int(row["count"])


def _candidate_record_from_input(value: Any, metadata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        record = dict(value)
        if "profile_url" not in record and record.get("canonical_profile_url"):
            record["profile_url"] = record["canonical_profile_url"]
        return record
    url = canonical_profile_url(value)
    if not url or not isinstance(metadata, dict) or metadata.get("is_private") is not False:
        return None
    handle = url.rstrip("/").split("/")[-1].lower()
    details = dict(metadata)
    if not details.get("source_name") or not details.get("source_record_key") or not details.get("observed_at"):
        return None
    if details.get("source_name") == "topsearch":
        source_key = str(details["source_record_key"])
        if not source_key.endswith(f":{handle}"):
            details["source_record_key"] = f"{source_key}:{handle}"
    details.setdefault("platform", "instagram")
    details.setdefault("username", handle)
    details["profile_url"] = url
    return details


def enqueue_candidates(
    store: CaptureStore,
    urls: Iterable[Any],
    *,
    priority: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
) -> int:
    added = 0
    for value in urls:
        record = _candidate_record_from_input(value, metadata)
        if record is None:
            continue
        try:
            if store.enqueue_candidate_job(record, priority=priority):
                added += 1
        except (TypeError, ValueError):
            continue
    return added


def enqueue_stale_refreshes(
    store: CaptureStore,
    *,
    stale_after_seconds: float = 24 * 60 * 60,
    limit: int = 10,
    now: Optional[float] = None,
) -> int:
    """Queue bounded refreshes for previously successful creator captures.

    Refresh idempotency is bucketed by the configured interval, so a healthy
    dispatcher can call this on every loop without duplicating work.
    """
    if stale_after_seconds <= 0:
        raise ValueError("stale_after_seconds must be positive")
    if limit <= 0:
        return 0
    current_time = time.time() if now is None else now
    cutoff = current_time - stale_after_seconds
    historical = store.connection.execute(
        """SELECT DISTINCT p.candidate_key, p.profile_url, p.captured_at
           FROM profile_captures p
           WHERE p.status IN ('complete', 'partial')
             AND NOT EXISTS (
                 SELECT 1 FROM profile_candidates c WHERE c.candidate_key = p.candidate_key
             )"""
    ).fetchall()
    for old in historical:
        handle = str(old["profile_url"]).rstrip("/").split("/")[-1].lower()
        if not handle:
            continue
        record = {
            "platform": "instagram",
            "username": handle,
            "profile_url": old["profile_url"],
            "is_private": False,
            "source_name": "historical_capture",
            "source_record_key": old["candidate_key"],
            "observed_at": old["captured_at"],
        }
        try:
            if store.upsert_candidate(record):
                store.mark_candidate(old["candidate_key"], "verified_public", "captured")
        except ValueError:
            continue
    rows = store.connection.execute(
        """SELECT c.candidate_key, c.profile_url, c.source_name, c.source_record_key,
                  c.source_observed_at, c.public_verified_at, c.niche, c.category,
                  MAX(p.captured_at) AS latest_capture
           FROM profile_captures p
           JOIN profile_candidates c ON c.candidate_key = p.candidate_key
           WHERE p.status IN ('complete', 'partial')
           GROUP BY c.candidate_key, c.profile_url, c.source_name,
                    c.source_record_key, c.source_observed_at, c.public_verified_at,
                    c.niche, c.category
           ORDER BY latest_capture ASC"""
    ).fetchall()
    added = 0
    bucket = int(current_time // stale_after_seconds)
    for row in rows:
        if added >= limit:
            break
        captured_at = str(row["latest_capture"])
        try:
            observed_at = datetime.fromisoformat(captured_at.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        if observed_at > cutoff:
            continue
        active = store.connection.execute(
            """SELECT 1 FROM collection_jobs
               WHERE status IN ('queued', 'leased')
                 AND json_extract(payload, '$.candidate_key') = ?
               LIMIT 1""",
            (row["candidate_key"],),
        ).fetchone()
        if active is not None:
            continue
        profile_url = str(row["profile_url"])
        handle = profile_url.rstrip("/").split("/")[-1].lower()
        if not handle:
            continue
        payload = {
            "platform": "instagram",
            "username": handle,
            "profile_url": profile_url,
            "canonical_profile_url": profile_url,
            "is_private": False,
            "source_name": row["source_name"],
            "source_record_key": row["source_record_key"],
            "observed_at": row["source_observed_at"],
            "candidate_key": row["candidate_key"],
            "public_verified_at": row["public_verified_at"],
            "reason": "stale_refresh",
        }
        if row["niche"] is not None:
            payload["niche"] = row["niche"]
        if row["category"] is not None:
            payload["category"] = row["category"]
        if store.enqueue_job(
            "capture_creator_profile",
            f"refresh:instagram:{handle}:{bucket}",
            payload,
            priority=1,
        ):
            store.connection.execute(
                """UPDATE profile_candidates SET status='queued', updated_at=?
                   WHERE candidate_key=? AND status IN ('captured', 'failed')""",
                (str(current_time), row["candidate_key"]),
            )
            store.connection.commit()
            added += 1
    return added


def cohort_status(store: CaptureStore, target: int) -> Dict[str, Any]:
    eligible = eligible_profile_count(store)
    candidate_counts = {
        row["status"]: row["count"]
        for row in store.connection.execute(
            "SELECT status, COUNT(*) AS count FROM profile_candidates GROUP BY status"
        )
    }
    cursor_row = store.connection.execute(
        "SELECT source_name, cursor, exhausted FROM source_cursors ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    rejection_count = store.connection.execute(
        """SELECT COUNT(*) FROM profile_candidates
           WHERE status='quarantined' AND last_error LIKE '%PUBLIC_PREFLIGHT_REJECTED%'"""
    ).fetchone()[0]
    return {
        "target": target,
        "eligible": eligible,
        "remaining": max(0, target - eligible),
        "queued_or_leased": queue_depth(store),
        "discovered": candidate_counts.get("discovered", 0),
        "verified_public": candidate_counts.get("verified_public", 0),
        "queued": candidate_counts.get("queued", 0),
        "leased": candidate_counts.get("leased", 0),
        "captured": candidate_counts.get("captured", 0),
        "quarantined": candidate_counts.get("quarantined", 0),
        "failed": candidate_counts.get("failed", 0),
        "exhausted": candidate_counts.get("exhausted", 0),
        "source_cursor": cursor_row["cursor"] if cursor_row else None,
        "source_exhausted": bool(cursor_row and cursor_row["exhausted"]),
        "public_preflight_rejections": rejection_count,
    }