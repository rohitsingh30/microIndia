"""Bounded local collection queue primitives and worker CLI.

The worker intentionally does not share Chrome profiles. It only leases jobs;
browser execution remains delegated to the existing E2E runner with explicit
per-worker profile configuration.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import time
from typing import Any, Dict, Optional

from .e2e import run as run_capture
from .store import CaptureStore


RETRYABLE_ERROR_CLASSES = {"TimeoutError", "ConnectionError", "RuntimeError"}
MAX_ATTEMPTS = 3


def _is_manual_auth_error(message: str) -> bool:
    text = message.lower()
    return any(term in text for term in ("authentication", "login", "challenge", "verification"))


def _retry_at(job: Dict[str, Any]) -> Optional[float]:
    if job["attempts"] >= MAX_ATTEMPTS:
        return None
    return time.time() + min(300, 2 ** job["attempts"] * 10)


SHARED_PROFILE = "/Users/rohit/playwright-chrome-profile-4"


def execute_job(
    job: Dict[str, Any],
    *,
    database: str,
    user_data_dir: Optional[str],
    profile_directory: str,
    cdp_url: Optional[str] = None,
    page_target_id: Optional[str] = None,
) -> Dict[str, Any]:
    if job["job_type"] != "capture_creator_profile":
        raise ValueError(f"Unsupported job type: {job['job_type']}")
    payload = job["payload"]
    required = ("candidate_key", "profile_url", "public_verified_at", "source_name")
    if any(not payload.get(field) for field in required):
        raise ValueError("capture job requires an explicit public candidate record")
    profile_url = payload.get("profile_url")
    if not profile_url:
        raise ValueError("capture_creator_profile requires payload.profile_url")
    if cdp_url is None and page_target_id is None:
        # Preserve the original runner call shape for local workers and tests.
        return asyncio.run(run_capture(profile_url, user_data_dir, profile_directory, database))
    return asyncio.run(
        run_capture(
            profile_url,
            user_data_dir,
            profile_directory,
            database,
            cdp_url=cdp_url,
            page_target_id=page_target_id,
        )
    )


def worker_once(
    database: str,
    worker_id: str,
    *,
    user_data_dir: Optional[str] = None,
    profile_directory: str = "Default",
    account_alias: str = "default",
    cdp_url: Optional[str] = None,
    page_target_id: Optional[str] = None,
    dry_run: bool = False,
) -> bool:
    store = CaptureStore(database)
    job = None
    attempt_id = None
    page_lease = None
    try:
        job = store.lease_job(worker_id)
        if not job:
            return False
        store.update_account_health(account_alias, status="busy", worker_id=worker_id)
        attempt_id = store.start_attempt(job["job_id"], worker_id)
        if dry_run:
            result = {"dry_run": True, "job_id": job["job_id"], "job_type": job["job_type"]}
            store.finish_attempt(attempt_id, outcome="dry_run")
            store.finish_job(job["job_id"], worker_id, outcome="complete")
            store.update_account_health(account_alias, status="healthy", worker_id=None)
            print(json.dumps({"worker_id": worker_id, "result": result}, sort_keys=True))
            return True
        if not cdp_url and not user_data_dir:
            raise ValueError("Browser jobs require --user-data-dir or --cdp-url")
        if not cdp_url and user_data_dir == SHARED_PROFILE:
            raise ValueError(
                f"{SHARED_PROFILE} cannot be used by queue workers directly; "
                "launch one Chrome owner and use --cdp-url plus a unique --page-target-id"
            )
        if cdp_url:
            page_lease = store.lease_browser_page(worker_id, target_id=page_target_id)
            if not page_lease:
                raise RuntimeError("No available shared browser page; retry when the browser owner registers a free tab")
            page_target_id = page_lease["target_id"]
        result = execute_job(
            job,
            database=database,
            user_data_dir=user_data_dir,
            profile_directory=profile_directory,
            cdp_url=cdp_url,
            page_target_id=page_target_id,
        )
        outcome = result.get("status", "complete")
        store.finish_attempt(attempt_id, outcome=outcome)
        if outcome == "needs_manual_auth":
            store.finish_job(job["job_id"], worker_id, outcome="needs_manual_auth", error="capture returned needs_manual_auth")
            store.update_account_health(account_alias, status="needs_manual_auth", worker_id=None, last_error="capture returned needs_manual_auth")
        else:
            store.finish_job(job["job_id"], worker_id, outcome="complete")
            store.update_account_health(account_alias, status="healthy", worker_id=None)
        print(json.dumps({"worker_id": worker_id, "result": result}, sort_keys=True))
        return True
    except Exception as exc:
        message = str(exc)
        error_class = type(exc).__name__
        manual_auth = _is_manual_auth_error(message)
        retry_at = None if manual_auth or error_class not in RETRYABLE_ERROR_CLASSES else _retry_at(job or {"attempts": MAX_ATTEMPTS})
        if attempt_id is not None:
            store.finish_attempt(
                attempt_id,
                outcome="needs_manual_auth" if manual_auth else "failed",
                error_class=error_class,
                error_message=message,
            )
        if job is not None:
            store.finish_job(
                job["job_id"],
                worker_id,
                outcome="needs_manual_auth" if manual_auth else "failed",
                error=f"{error_class}: {message}",
                retry_at=retry_at,
            )
            store.update_account_health(
                account_alias,
                status="needs_manual_auth" if manual_auth else "cooldown" if retry_at else "error",
                worker_id=None,
                cooldown_until=retry_at,
                last_error=f"{error_class}: {message}",
            )
        print(json.dumps({"worker_id": worker_id, "job_id": job["job_id"] if job else None, "error": message, "retry_at": retry_at}, sort_keys=True))
        return True
    finally:
        if page_lease is not None:
            store.release_browser_page(page_lease["target_id"], worker_id)
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one bounded microIndia collection queue worker")
    parser.add_argument("--database", default=os.environ.get("MICROINDIA_DATABASE", "data/microindia.sqlite3"))
    parser.add_argument("--worker-id", default=f"{socket.gethostname()}-{os.getpid()}")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--max-jobs", type=int, default=1)
    parser.add_argument("--user-data-dir", default=os.environ.get("MICROINDIA_CHROME_DATA_DIR"))
    parser.add_argument("--profile-directory", default=os.environ.get("MICROINDIA_CHROME_PROFILE", "Default"))
    parser.add_argument("--cdp-url", default=os.environ.get("MICROINDIA_CDP_URL"), help="Attach to the single shared Chrome owner")
    parser.add_argument("--page-target-id", default=os.environ.get("MICROINDIA_PAGE_TARGET_ID"), help="Dedicated tab target for this worker")
    parser.add_argument("--account-alias", default=os.environ.get("MICROINDIA_ACCOUNT_ALIAS", "default"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if not args.dry_run and not args.user_data_dir and not args.cdp_url:
        parser.error("--user-data-dir or --cdp-url is required unless --dry-run is supplied")
    processed = 0
    while args.max_jobs <= 0 or processed < args.max_jobs:
        did_work = worker_once(
            args.database,
            args.worker_id,
            user_data_dir=args.user_data_dir,
            profile_directory=args.profile_directory,
            account_alias=args.account_alias,
            cdp_url=args.cdp_url,
            page_target_id=args.page_target_id,
            dry_run=args.dry_run,
        )
        if args.once:
            return
        if did_work:
            processed += 1
            time.sleep(args.poll_seconds)
        elif args.max_jobs <= 0:
            time.sleep(args.poll_seconds)
        else:
            return


if __name__ == "__main__":
    main()