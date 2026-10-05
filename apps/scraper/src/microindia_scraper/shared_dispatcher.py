"""Single-process dispatcher for multiple jobs/pages in one shared Chrome session."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import socket
import time
import uuid
from typing import Optional

from browser_use import Browser

from .candidate_source import read_ndjson_batch
from .browser_owner import _target_id
from .e2e import run as run_capture
from .cohort import (
    DISCOVERY_SLOT_COUNT,
    canonical_profile_url,
    cohort_status,
    deterministic_slot_queries,
    discover_for_you_profile_urls,
    discover_profile_urls,
    enqueue_candidates,
    enqueue_stale_refreshes,
)
from .queue import RETRYABLE_ERROR_CLASSES, _is_manual_auth_error, _retry_at
from .store import CaptureStore
IDLE_CAPTURE_SLOT_URL = "http://127.0.0.1:8787/"
DISCOVERY_IDLE_SECONDS = 60.0


def _candidate_timestamp(value: object) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            from datetime import datetime
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


async def preflight_public_candidate(page: object, candidate: dict) -> dict:
    """Check explicit public evidence without navigating the discovery page."""
    profile_url = candidate.get("profile_url") or candidate.get("canonical_profile_url")
    canonical_url = canonical_profile_url(profile_url)
    observed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if not canonical_url or canonical_url != profile_url:
        return {"status": "unknown", "evidence": "MALFORMED_PROFILE_URL", "observed_at": observed_at}
    if "verification_expires_at" in candidate:
        expiry = _candidate_timestamp(candidate.get("verification_expires_at"))
        if expiry is None:
            return {"status": "unknown", "evidence": "PUBLIC_VERIFICATION_INVALID", "observed_at": observed_at}
        if expiry < time.time():
            return {"status": "unknown", "evidence": "PUBLIC_VERIFICATION_EXPIRED", "observed_at": observed_at}
    import json
    try:
        result = await page.evaluate(
            f"""() => fetch({json.dumps(canonical_url)}, {{credentials: 'include'}}).then(async response => {{
              const body = await response.text();
              let data = null;
              try {{ data = JSON.parse(body); }} catch (_) {{}}
              return {{status: response.status, final_url: response.url, body: body.slice(0, 20000), data}};
            }})"""
        )
    except Exception:
        return {"status": "unknown", "evidence": "PREFLIGHT_EVALUATION_FAILED", "observed_at": observed_at}
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            result = {"body": result}
    if not isinstance(result, dict):
        return {"status": "unknown", "evidence": "PREFLIGHT_UNSTRUCTURED_RESPONSE", "observed_at": observed_at}
    final_url = str(result.get("final_url") or "").lower()
    body = str(result.get("body") or "")
    lowered = (final_url + "\n" + body).lower()
    if re.search(r"/accounts/login|/challenge|/checkpoint|log\s*in\s+to\s+instagram|login\s+required", lowered):
        return {"status": "auth_required", "evidence": "PREFLIGHT_AUTH_REQUIRED", "observed_at": observed_at}
    data = result.get("data")
    if not isinstance(data, dict) and ("is_private" in result or "isPrivate" in result):
        data = result
    if isinstance(data, dict):
        inspected = [data, data.get("user"), data.get("profile"), data.get("data")]
        for item in inspected:
            if not isinstance(item, dict):
                continue
            nested = [item, item.get("user"), item.get("profile")]
            for value in nested:
                if not isinstance(value, dict):
                    continue
                if value.get("is_private") is True or value.get("isPrivate") is True:
                    return {"status": "private", "evidence": "PREFLIGHT_PRIVATE_MARKER", "observed_at": observed_at}
                if value.get("is_private") is False or value.get("isPrivate") is False:
                    try:
                        http_status = int(result.get("status") or 200)
                    except (TypeError, ValueError):
                        http_status = 200
                    if 200 <= http_status < 300:
                        return {"status": "public", "evidence": "PREFLIGHT_EXPLICIT_PUBLIC", "observed_at": observed_at}
                    break
    if re.search(r"\b(private account|this account is private)\b", body, re.I) or re.search(
        r"""["']?is[_-]?private["']?\s*:\s*true\b""", body, re.I
    ):
        return {"status": "private", "evidence": "PREFLIGHT_PRIVATE_MARKER", "observed_at": observed_at}
    if re.search(r"""["']?is[_-]?private["']?\s*:\s*false\b""", body, re.I):
        try:
            http_status = int(result.get("status") or 200)
        except (TypeError, ValueError):
            http_status = 200
        if 200 <= http_status < 300:
            return {"status": "public", "evidence": "PREFLIGHT_HTML_EXPLICIT_PUBLIC", "observed_at": observed_at}
    try:
        http_status = int(result.get("status") or 200)
    except (TypeError, ValueError):
        http_status = 200
    if http_status in {401, 403}:
        return {"status": "auth_required", "evidence": "PREFLIGHT_AUTH_STATUS", "observed_at": observed_at}
    if (
        candidate.get("discovery_surface") == "instagram_reels_for_you"
        and candidate.get("public_evidence") == "FYP_VISIBLE_REEL_AUTHOR"
    ):
        return {"status": "public", "evidence": "FYP_VISIBLE_REEL_AUTHOR", "observed_at": observed_at}
    return {"status": "unknown", "evidence": "PREFLIGHT_MISSING_EXPLICIT_PUBLIC_EVIDENCE", "observed_at": observed_at}


def stable_shard_assignment(candidate_key: str, shard_count: int) -> int:
    import hashlib
    if shard_count < 1:
        raise ValueError("shard_count must be positive")
    return int.from_bytes(hashlib.sha256(candidate_key.encode("utf-8")).digest()[:8], "big") % shard_count

def _source_cursor_name(source_path: str, shard_id: int = 0, shard_count: int = 1) -> str:
    source_name = os.path.abspath(source_path)
    if shard_count == 1:
        return source_name
    return f"{source_name}#shard-{shard_id}-of-{shard_count}"

def ingest_source_batch(
    store: CaptureStore,
    source_path: str,
    *,
    batch_size: int = 500,
    shard_id: int = 0,
    shard_count: int = 1,
) -> dict:
    source_name = os.path.abspath(source_path)
    cursor_name = _source_cursor_name(source_path, shard_id, shard_count)
    result = {
        "source": source_name,
        "cursor_source": cursor_name,
        "batch": 0,
        "accepted": 0,
        "jobs_added": 0,
        "quarantined": 0,
        "rejected": 0,
        "cursor": store.load_source_cursor(cursor_name) or "0",
        "exhausted": False,
    }
    if not source_path or not os.path.isfile(source_path):
        result["paused"] = "missing_source"
        return result
    rows = list(read_ndjson_batch(source_path, result["cursor"], batch_size))
    if not rows:
        store.mark_source_exhausted(cursor_name)
        result["exhausted"] = True
        return result
    seen_source_keys = set()
    for line_start, next_cursor, record, error, raw_line in rows:
        result["batch"] += 1
        result["cursor"] = str(next_cursor)
        if error is not None:
            store.record_source_rejection(cursor_name, line_start, next_cursor, raw_line, error)
            result["rejected"] += 1
            continue
        assert record is not None
        if stable_shard_assignment(record.candidate_key, shard_count) != shard_id:
            continue
        identity = (record.source_name, record.source_record_key)
        existing = store.connection.execute(
            "SELECT 1 FROM profile_candidates WHERE source_name=? AND source_record_key=?",
            identity,
        ).fetchone()
        if identity in seen_source_keys or existing is not None:
            store.record_source_rejection(
                cursor_name, line_start, next_cursor, raw_line,
                "duplicate source identity or conflicting candidate",
            )
            result["rejected"] += 1
            continue
        seen_source_keys.add(identity)
        payload = record.as_dict()
        if not store.upsert_candidate(payload):
            store.record_source_rejection(
                cursor_name, line_start, next_cursor, raw_line,
                "duplicate source identity or conflicting candidate",
            )
            result["rejected"] += 1
            continue
        result["accepted"] += 1
        candidate_row = store.connection.execute(
            "SELECT status FROM profile_candidates WHERE candidate_key=?",
            (record.candidate_key,),
        ).fetchone()
        if candidate_row and candidate_row["status"] == "quarantined":
            result["quarantined"] += 1
            continue
        if store.enqueue_candidate_job(payload, priority=5):
            result["jobs_added"] += 1
    store.save_source_cursor(cursor_name, result["cursor"])
    if int(result["cursor"]) >= os.path.getsize(source_path):
        store.mark_source_exhausted(cursor_name)
        result["exhausted"] = True
    return result

FOR_YOU_SOURCE = "instagram-for-you"
FOR_YOU_SOURCE_ALIASES = {FOR_YOU_SOURCE, "for-you", "reels"}


def is_for_you_source(source: str) -> bool:
    return str(source or "").strip().lower() in FOR_YOU_SOURCE_ALIASES


async def ingest_for_you_batch(
    page: object,
    store: CaptureStore,
    *,
    limit: int = 100,
) -> dict:
    """Discover visible For You authors, then enqueue only public-preflighted profiles."""
    current_url = str(getattr(page, "url", "") or "")
    if "/reels" not in current_url.lower():
        await page.goto("https://www.instagram.com/reels/")
    urls = await discover_for_you_profile_urls(page, limit=limit)
    result = {
        "source": FOR_YOU_SOURCE,
        "candidates": len(urls),
        "public": 0,
        "rejected": 0,
        "jobs_added": 0,
    }
    for profile_url in urls:
        preflight = await preflight_public_candidate(page, {
            "profile_url": profile_url,
            "discovery_surface": "instagram_reels_for_you",
            "public_evidence": "FYP_VISIBLE_REEL_AUTHOR",
        })
        status = preflight.get("status")
        if status == "auth_required":
            raise RuntimeError("Instagram requires manual authentication in the local Chrome profile")
        if status != "public":
            result["rejected"] += 1
            continue
        username = profile_url.rstrip("/").rsplit("/", 1)[-1].lower()
        record = {
            "platform": "instagram",
            "username": username,
            "profile_url": profile_url,
            "is_private": False,
            "source_name": FOR_YOU_SOURCE,
            "source_record_key": f"for-you:{username}",
            "observed_at": preflight["observed_at"],
            "discovery_surface": "instagram_reels_for_you",
            "public_evidence": preflight.get("evidence"),
        }
        if store.enqueue_candidate_job(record, priority=5):
            result["jobs_added"] += 1
        result["public"] += 1
    return result


def source_ingestion_allowed(queue_depth_value: int, high_water: int, low_water: int, paused: bool) -> tuple[bool, bool]:
    if high_water < 1 or low_water < 0 or low_water >= high_water:
        raise ValueError("low_water must be below high_water")
    if paused:
        if queue_depth_value > low_water:
            return False, True
        return True, False
    if queue_depth_value >= high_water:
        return False, True
    return True, False

async def sync_pages(
    browser: Browser,
    store: CaptureStore,
    owner_id: str,
    minimum_pages: int,
    cdp_url: str = "",
) -> list[str]:
    pages = await browser.get_pages()
    visible_pages = [page for page in pages if getattr(page, "target_id", None) or getattr(page, "_target_id", None)]
    while len(visible_pages) < minimum_pages:
        visible_pages.append(await browser.new_page())
    discovery_page = visible_pages[0]
    if str(getattr(discovery_page, "url", "")) in {"", "about:blank", "chrome://newtab/", "chrome://new-tab-page/"}:
        try:
            await discovery_page.goto("https://www.instagram.com/")
        except Exception:
            pass
    for page in visible_pages[1:]:
        if str(getattr(page, "url", "")) in {"", "about:blank", "chrome://newtab/", "chrome://new-tab-page/"}:
            try:
                await page.goto(IDLE_CAPTURE_SLOT_URL)
            except Exception:
                pass
    target_ids = [_target_id(page) for page in visible_pages]
    for target_id in target_ids:
        store.register_browser_page(target_id, owner_id)
    store.mark_missing_browser_pages(owner_id, target_ids)
    store.retire_browser_pages(owner_id, target_ids)
    store.heartbeat_browser_owner(
        owner_id,
        status="healthy",
        cdp_url=cdp_url or None,
        minimum_pages=minimum_pages,
        page_count=len(target_ids),
    )
    return target_ids

async def dispatch_once(
    browser: Browser,
    store: CaptureStore,
    owner_id: str,
    database: str,
    *,
    discovery_target_id: Optional[str] = None,
    shard_id: int = 0,
    shard_count: int = 1,
) -> bool:
    worker_id = f"{owner_id}:job-{uuid.uuid4().hex[:8]}"
    job = store.lease_job(worker_id, shard_id=shard_id, shard_count=shard_count)
    if not job:
        return False
    attempt_id: Optional[int] = None
    page_lease: Optional[dict] = None
    candidate_key = job["payload"].get("candidate_key")
    try:
        attempt_id = store.start_attempt(job["job_id"], worker_id)
        payload = job["payload"]
        required = ("candidate_key", "profile_url", "public_verified_at", "source_name")
        if job["job_type"] != "capture_creator_profile":
            raise ValueError(f"Unsupported job type: {job['job_type']}")
        if any(not payload.get(field) for field in required):
            raise ValueError("capture job requires an explicit public candidate record")
        canonical_url = canonical_profile_url(payload.get("profile_url"))
        if not canonical_url or canonical_url != payload["profile_url"]:
            raise ValueError(f"Invalid canonical profile URL: {payload.get('profile_url')!r}")
        candidate = store.lease_candidate(candidate_key, worker_id)
        if not candidate:
            raise ValueError("candidate is unavailable or already terminal")
        if (
            candidate["profile_url"] != canonical_url
            or candidate["source_name"] != payload["source_name"]
            or candidate["source_record_key"] != payload.get("source_record_key")
            or candidate["public_verified_at"] != payload["public_verified_at"]
        ):
            raise ValueError("capture job does not match its persisted public candidate")
        pages = await browser.get_pages()
        discovery_page = next(
            (page for page in pages if _target_id(page) == discovery_target_id),
            pages[0] if pages else None,
        )
        if discovery_page is None:
            store.mark_candidate_owned(candidate_key, worker_id, "leased", "queued", error="discovery page unavailable")
            raise RuntimeError("No discovery page available")
        preflight = await preflight_public_candidate(discovery_page, {
            **candidate,
            **payload,
            "profile_url": canonical_url,
        })
        preflight_status = preflight["status"]
        if preflight_status in {"private", "unknown"}:
            reason = json.dumps({
                "reason": "PUBLIC_PREFLIGHT_REJECTED",
                "status": preflight_status,
                "evidence": preflight.get("evidence"),
                "observed_at": preflight.get("observed_at"),
            }, sort_keys=True)
            store.mark_candidate_owned(candidate_key, worker_id, "leased", "quarantined", error=reason)
            store.finish_attempt(attempt_id, outcome="quarantined", error_class="PublicPreflightRejected", error_message=reason)
            store.finish_job(job["job_id"], worker_id, outcome="failed", error=reason)
            print({"event": "candidate_quarantined", "job_id": job["job_id"], "candidate_key": candidate_key, "reason": json.loads(reason)}, flush=True)
            return True
        if preflight_status == "auth_required":
            store.mark_candidate_owned(candidate_key, worker_id, "leased", "queued", error="PREFLIGHT_AUTH_REQUIRED")
            store.finish_attempt(attempt_id, outcome="needs_manual_auth", error_class="AuthenticationRequired", error_message="PREFLIGHT_AUTH_REQUIRED")
            store.finish_job(job["job_id"], worker_id, outcome="needs_manual_auth", error="PREFLIGHT_AUTH_REQUIRED")
            store.heartbeat_browser_owner(owner_id, status="needs_manual_auth", last_error="PREFLIGHT_AUTH_REQUIRED")
            return True
        page_lease = store.lease_browser_page(worker_id, exclude_target_id=discovery_target_id)
        if not page_lease:
            store.mark_candidate_owned(candidate_key, worker_id, "leased", "queued", error="No available shared browser page")
            retry_at = time.time() + 5
            store.finish_job(job["job_id"], worker_id, outcome="failed", error="No available shared browser page", retry_at=retry_at)
            store.finish_attempt(attempt_id, outcome="failed", error_class="NoPage", error_message="No available shared browser page")
            return True
        page = next(page for page in await browser.get_pages() if _target_id(page) == page_lease["target_id"])
        result = await run_capture(
            canonical_url,
            None,
            "Default",
            database,
            browser_session=browser,
            page=page,
            requested_niche=payload.get("niche"),
        )
        outcome = result.get("status", "complete")
        if outcome == "quarantined":
            store.mark_candidate_owned(candidate_key, worker_id, "leased", "quarantined", error=result.get("evidence"))
        elif outcome == "needs_manual_auth":
            store.mark_candidate_owned(candidate_key, worker_id, "leased", "queued", error="MANUAL_AUTH_RECHECK")
        else:
            store.mark_candidate_owned(candidate_key, worker_id, "leased", "captured")
        store.finish_attempt(attempt_id, outcome=outcome)
        store.finish_job(
            job["job_id"],
            worker_id,
            outcome="needs_manual_auth" if outcome == "needs_manual_auth" else "complete",
            error="capture returned needs_manual_auth" if outcome == "needs_manual_auth" else None,
        )
        print({
            "event": "capture_finished",
            "worker_id": worker_id,
            "job_id": job["job_id"],
            "candidate_key": candidate_key,
            "profile_url": canonical_url,
            "page_target_id": page_lease["target_id"],
            "capture_id": result.get("capture_id"),
            "status": outcome,
        }, flush=True)
    except Exception as exc:
        message = str(exc)
        error_class = type(exc).__name__
        manual_auth = _is_manual_auth_error(message)
        retry_at = None if manual_auth or error_class not in RETRYABLE_ERROR_CLASSES else _retry_at(job)
        if candidate_key:
            if manual_auth:
                store.mark_candidate_owned(candidate_key, worker_id, "leased", "queued", error=message)
            else:
                store.mark_candidate_owned(candidate_key, worker_id, "leased", "failed", error=message)
        if attempt_id is not None:
            store.finish_attempt(
                attempt_id,
                outcome="needs_manual_auth" if manual_auth else "failed",
                error_class=error_class,
                error_message=message,
            )
        store.finish_job(
            job["job_id"],
            worker_id,
            outcome="needs_manual_auth" if manual_auth else "failed",
            error=f"{error_class}: {message}",
            retry_at=retry_at,
        )
        if manual_auth:
            store.heartbeat_browser_owner(owner_id, status="needs_manual_auth", last_error=message)
        print({
            "event": "capture_failed",
            "worker_id": worker_id,
            "job_id": job["job_id"],
            "candidate_key": candidate_key,
            "profile_url": job["payload"].get("profile_url"),
            "page_target_id": page_lease["target_id"] if page_lease else None,
            "error_class": error_class,
            "error": repr(exc),
            "retry_at": retry_at,
        }, flush=True)
    finally:
        if page_lease:
            store.release_browser_page(page_lease["target_id"], worker_id)
    return True


async def dispatch_batch(
    browser: Browser,
    store: CaptureStore,
    owner_id: str,
    database: str,
    batch_size: int,
    *,
    discovery_target_id: Optional[str] = None,
    shard_id: int = 0,
    shard_count: int = 1,
) -> list[bool]:
    if batch_size < 1:
        return []
    return list(await asyncio.gather(*(
        dispatch_once(
            browser,
            store,
            owner_id,
            database,
            discovery_target_id=discovery_target_id,
            shard_id=shard_id,
            shard_count=shard_count,
        )
        for _ in range(batch_size)
    )))
async def discover_until_buffered(
    browser: Browser,
    store: CaptureStore,
    target_profiles: int,
    round_index: int,
    refresh_after_seconds: float,
    refresh_limit: int,
    selection_seed: int,
    discovery_target_id: Optional[str],
    owner_id: str,
    minimum_pages: int,
    cdp_url: str,
    buffer_limit: int = 3,
    discovery_slots_per_cycle: int = 1,
) -> int:
    refreshed = enqueue_stale_refreshes(
        store,
        stale_after_seconds=refresh_after_seconds,
        limit=refresh_limit,
    )
    if refreshed:
        print({"stale_refresh_jobs_added": refreshed}, flush=True)
    status = cohort_status(store, target_profiles)
    if status["eligible"] >= target_profiles or status["queued_or_leased"] >= buffer_limit:
        return round_index
    pages = await browser.get_pages()
    if not pages:
        return round_index
    page = next(
        (candidate for candidate in pages if _target_id(candidate) == discovery_target_id),
        pages[0],
    )
    await page.goto("https://www.instagram.com/")
    store.heartbeat_browser_owner(
        owner_id,
        status="healthy",
        cdp_url=cdp_url,
        minimum_pages=minimum_pages,
        page_count=len(pages),
    )
    query_round = round_index // DISCOVERY_SLOT_COUNT
    slot_index = round_index % DISCOVERY_SLOT_COUNT
    assignments = deterministic_slot_queries(selection_seed, query_round)
    for assignment in assignments[slot_index:slot_index + max(1, discovery_slots_per_cycle)]:
        query = assignment["niche"]
        try:
            urls = await discover_profile_urls(page, query)
            added = enqueue_candidates(
                store,
                urls,
                priority=5,
                metadata={
                    **assignment,
                    "is_private": False,
                    "source_name": "topsearch",
                    "source_record_key": f"topsearch:{query}",
                    "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
            )
            print({
                "event": "discovery_finished",
                "agent_slot": assignment["agent_slot"],
                "niche": query,
                "selection_seed": selection_seed,
                "round_index": query_round,
                "candidates": len(urls),
                "jobs_added": added,
                "status": cohort_status(store, target_profiles),
            }, flush=True)
        except Exception as exc:
            if _is_manual_auth_error(str(exc)):
                raise
            print({
                "event": "discovery_failed",
                "agent_slot": assignment["agent_slot"],
                "niche": query,
                "error_class": type(exc).__name__,
                "error": repr(exc),
            }, flush=True)
        store.heartbeat_browser_owner(
            owner_id,
            status="healthy",
            cdp_url=cdp_url,
            minimum_pages=minimum_pages,
            page_count=len(pages),
        )
        status = cohort_status(store, target_profiles)
        if status["eligible"] >= target_profiles or status["queued_or_leased"] >= buffer_limit:
            break
    return round_index + 1


async def run(
    cdp_url: str,
    database: str,
    owner_id: str,
    minimum_pages: int,
    poll_seconds: float,
    max_jobs: int,
    target_profiles: int,
    *,
    refresh_after_seconds: float = 24 * 60 * 60,
    refresh_limit: int = 10,
    stop_at_target: bool = False,
    discovery_only: bool = False,
    capture_concurrency: int = 3,
    selection_seed: int = 20260326,
    candidate_source: str = "",
    queue_buffer: int = 1000,
    source_batch_size: int = 500,
    queue_low_water: int = 250,
    shard_id: int = 0,
    shard_count: int = 1,
) -> None:
    if minimum_pages < 1:
        raise ValueError("minimum_pages must be at least 1")
    if capture_concurrency < 1:
        raise ValueError("capture_concurrency must be at least 1")
    if refresh_after_seconds <= 0:
        raise ValueError("refresh_after_seconds must be positive")
    if refresh_limit < 1:
        raise ValueError("refresh_limit must be at least 1")
    if source_batch_size < 1:
        raise ValueError("source_batch_size must be positive")
    if shard_count < 1 or not 0 <= shard_id < shard_count:
        raise ValueError("invalid shard assignment")
    store = CaptureStore(database)
    processed = 0
    ingestion_paused = False
    pause_reason: Optional[str] = None
    discovery_round = 0
    candidate_source = candidate_source or ""
    if candidate_source.strip() == "${MICROINDIA_PUBLIC_CANDIDATE_SOURCE}":
        candidate_source = ""
    topsearch_mode = candidate_source.strip().lower() == "topsearch"
    # Feed/reels discovery is intentionally not a candidate source. The only
    # automatic source is the approved public NDJSON index; topsearch remains
    # an explicit low-volume authenticated adapter.
    for_you_mode = False
    source_name = (
        "topsearch"
        if topsearch_mode
        else _source_cursor_name(candidate_source, shard_id, shard_count) if candidate_source else ""
    )
    try:
        while max_jobs <= 0 or processed < max_jobs:
            browser = Browser(cdp_url=cdp_url)
            try:
                await browser.start()
                while max_jobs <= 0 or processed < max_jobs:
                    owner_before_sync = store.connection.execute(
                        "SELECT status FROM browser_owners WHERE browser_owner_id=?",
                        (owner_id,),
                    ).fetchone()
                    if owner_before_sync and owner_before_sync["status"] == "needs_manual_auth":
                        if pause_reason != "authentication":
                            print({"event": "collection_paused", "reason": "authentication"}, flush=True)
                            pause_reason = "authentication"
                        await asyncio.sleep(300.0)
                        continue
                    target_ids = await sync_pages(browser, store, owner_id, minimum_pages, cdp_url)
                    discovery_target_id = target_ids[0] if target_ids else None
                    depth = int(cohort_status(store, target_profiles)["queued_or_leased"])
                    allowed, ingestion_paused = source_ingestion_allowed(
                        depth, queue_buffer, queue_low_water, ingestion_paused
                    )
                    if topsearch_mode and allowed:
                        discovery_round = await discover_until_buffered(
                            browser,
                            store,
                            target_profiles,
                            discovery_round,
                            refresh_after_seconds,
                            refresh_limit,
                            selection_seed,
                            discovery_target_id,
                            owner_id,
                            minimum_pages,
                            cdp_url,
                            buffer_limit=queue_buffer,
                            discovery_slots_per_cycle=1,
                        )
                        pause_reason = None
                    elif not candidate_source:
                        if pause_reason != "missing_source":
                            print({"event": "collection_paused", "reason": "missing_source"}, flush=True)
                            pause_reason = "missing_source"
                    elif allowed and os.path.isfile(candidate_source):
                        if store.source_is_exhausted(source_name):
                            cursor = int(store.load_source_cursor(source_name) or "0")
                            if os.path.getsize(candidate_source) > cursor:
                                store.save_source_cursor(source_name, str(cursor))
                        if not store.source_is_exhausted(source_name):
                            batch = ingest_source_batch(
                                store,
                                candidate_source,
                                batch_size=min(source_batch_size, max(1, queue_buffer - depth)),
                                shard_id=shard_id,
                                shard_count=shard_count,
                            )
                            print({
                                "event": "candidate_ingested",
                                **batch,
                                "queue_depth": cohort_status(store, target_profiles)["queued_or_leased"],
                            }, flush=True)
                            if batch.get("rejected"):
                                print({
                                    "event": "candidate_rejected",
                                    "source": batch["source"],
                                    "count": batch["rejected"],
                                    "cursor": batch["cursor"],
                                }, flush=True)
                            if batch.get("quarantined"):
                                print({
                                    "event": "candidate_quarantined",
                                    "source": batch["source"],
                                    "count": batch["quarantined"],
                                    "cursor": batch["cursor"],
                                }, flush=True)
                            if batch.get("exhausted"):
                                print({"event": "candidate_source_exhausted", **batch}, flush=True)
                                pause_reason = "source_exhausted"
                        elif pause_reason != "source_exhausted":
                            print({"event": "collection_paused", "reason": "source_exhausted"}, flush=True)
                            pause_reason = "source_exhausted"
                    elif for_you_mode and ingestion_paused and pause_reason != "queue_high_water":
                        print({"event": "collection_paused", "reason": "queue_high_water", "queue_depth": depth}, flush=True)
                        pause_reason = "queue_high_water"
                    elif candidate_source and not topsearch_mode and not for_you_mode and not os.path.isfile(candidate_source):
                        if pause_reason != "missing_source":
                            print({"event": "collection_paused", "reason": "missing_source", "source": candidate_source}, flush=True)
                            pause_reason = "missing_source"
                    elif ingestion_paused and pause_reason != "queue_high_water":
                        print({"event": "collection_paused", "reason": "queue_high_water", "queue_depth": depth}, flush=True)
                        pause_reason = "queue_high_water"
                    else:
                        pause_reason = None
                    refreshed = enqueue_stale_refreshes(
                        store,
                        stale_after_seconds=refresh_after_seconds,
                        limit=refresh_limit,
                    )
                    if refreshed:
                        print({"event": "stale_refresh_jobs_added", "count": refreshed}, flush=True)
                    status = cohort_status(store, target_profiles)
                    if stop_at_target and status["eligible"] >= target_profiles:
                        print({"cohort_complete": status}, flush=True)
                        return
                    if discovery_only:
                        await asyncio.sleep(poll_seconds)
                        continue
                    owner_row = store.connection.execute(
                        "SELECT status FROM browser_owners WHERE browser_owner_id=?",
                        (owner_id,),
                    ).fetchone()
                    if owner_row and owner_row["status"] == "needs_manual_auth":
                        if pause_reason != "authentication":
                            print({"event": "collection_paused", "reason": "authentication"}, flush=True)
                            pause_reason = "authentication"
                        await asyncio.sleep(300.0)
                        continue
                    remaining_jobs = max_jobs - processed if max_jobs > 0 else capture_concurrency
                    results = await dispatch_batch(
                        browser,
                        store,
                        owner_id,
                        database,
                        min(capture_concurrency, remaining_jobs),
                        discovery_target_id=discovery_target_id,
                        shard_id=shard_id,
                        shard_count=shard_count,
                    )
                    did_work = sum(1 for result in results if result)
                    processed += did_work
                    if did_work == 0:
                        await asyncio.sleep(poll_seconds)
            except Exception as exc:
                manual_auth = _is_manual_auth_error(str(exc))
                owner_status = "needs_manual_auth" if manual_auth else "degraded"
                store.heartbeat_browser_owner(
                    owner_id,
                    status=owner_status,
                    minimum_pages=minimum_pages,
                    last_error=f"{type(exc).__name__}: {exc}",
                )
                reason = "authentication" if manual_auth else "browser_owner_degraded"
                if pause_reason != reason:
                    print({
                        "event": "collection_paused",
                        "reason": reason,
                        "error_class": type(exc).__name__,
                        "error": repr(exc),
                    }, flush=True)
                    pause_reason = reason
                await asyncio.sleep(300.0 if manual_auth else max(5.0, poll_seconds))
            finally:
                try:
                    await browser.stop()
                except Exception:
                    pass
                store.heartbeat_browser_owner(owner_id, status="stopped", minimum_pages=minimum_pages)
    finally:
        store.close()

def main() -> None:
    parser = argparse.ArgumentParser(description="Run collection jobs through one shared Browser Use/CDP session")
    parser.add_argument("--cdp-url", default=os.environ.get("MICROINDIA_CDP_URL", "http://127.0.0.1:9222"))
    parser.add_argument("--database", default=os.environ.get("MICROINDIA_DATABASE", "data/microindia.sqlite3"))
    parser.add_argument("--owner-id", default=f"{socket.gethostname()}-{os.getpid()}")
    parser.add_argument("--min-pages", type=int, default=2)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--max-jobs", type=int, default=0, help="Maximum jobs; zero means run continuously")
    parser.add_argument("--target-profiles", type=int, default=1000000, help="Cohort target; refreshes continue after this target")
    parser.add_argument("--refresh-after-hours", type=float, default=24.0)
    parser.add_argument("--refresh-limit", type=int, default=10)
    parser.add_argument("--stop-at-target", action="store_true")
    parser.add_argument("--discovery-only", action="store_true", help="Only discover and queue profiles; capture agents consume the queue")
    parser.add_argument("--capture-concurrency", type=int, default=3, help="Maximum simultaneous deep profile captures")
    parser.add_argument("--selection-seed", type=int, default=20260326)
    parser.add_argument("--candidate-source", default=os.environ.get("MICROINDIA_PUBLIC_CANDIDATE_SOURCE", ""))
    parser.add_argument("--queue-buffer", type=int, default=1000)
    parser.add_argument("--source-batch-size", type=int, default=500)
    parser.add_argument("--queue-low-water", type=int, default=250)
    parser.add_argument("--shard-id", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    args = parser.parse_args()
    try:
        asyncio.run(run(
            args.cdp_url,
            args.database,
            args.owner_id,
            args.min_pages,
            args.poll_seconds,
            args.max_jobs,
            args.target_profiles,
            refresh_after_seconds=args.refresh_after_hours * 60 * 60,
            refresh_limit=args.refresh_limit,
            stop_at_target=args.stop_at_target,
            discovery_only=args.discovery_only,
            capture_concurrency=args.capture_concurrency,
            selection_seed=args.selection_seed,
            candidate_source=args.candidate_source,
            queue_buffer=args.queue_buffer,
            source_batch_size=args.source_batch_size,
            queue_low_water=args.queue_low_water,
            shard_id=args.shard_id,
            shard_count=args.shard_count,
        ))
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()