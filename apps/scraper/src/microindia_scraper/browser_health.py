"""Non-invasive local Chrome/CDP and browser-page health checks."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen


DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_DATABASE = "data/microindia.sqlite3"
DEFAULT_HEALTH_FILE = "run/browser-health.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_json(url: str, timeout_seconds: float) -> Any:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _cdp_health(cdp_url: str, timeout_seconds: float) -> Dict[str, Any]:
    base = cdp_url.rstrip("/")
    try:
        version = _fetch_json(f"{base}/json/version", timeout_seconds)
        targets = _fetch_json(f"{base}/json/list", timeout_seconds)
        pages = [target for target in targets if target.get("type") == "page"] if isinstance(targets, list) else []
        return {
            "reachable": True,
            "browser": version.get("Browser"),
            "protocol_version": version.get("Protocol-Version"),
            "page_count": len(pages),
            "targets": [
                {"id": target.get("id"), "title": target.get("title"), "url": target.get("url")}
                for target in pages
            ],
        }
    except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return {"reachable": False, "error": f"{type(exc).__name__}: {exc}"}


def _database_health(database: str, timeout_seconds: float, owner_stale_seconds: float) -> Dict[str, Any]:
    connection: Optional[sqlite3.Connection] = None
    try:
        connection = sqlite3.connect(database, timeout=timeout_seconds)
        connection.row_factory = sqlite3.Row
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        pages = connection.execute(
            """SELECT target_id, status, leased_by, lease_until, last_seen_at
               FROM browser_pages ORDER BY target_id"""
        ).fetchall()
        schema_missing = False
        try:
            owners = connection.execute(
                """SELECT browser_owner_id, status, cdp_url, minimum_pages, page_count,
                          last_heartbeat_at, last_error, updated_at
                   FROM browser_owners ORDER BY browser_owner_id"""
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            owners = []
            schema_missing = True
        now = time.time()
        available = sum(1 for page in pages if page["status"] == "available")
        stale = sum(1 for page in pages if page["last_seen_at"] is None or now - page["last_seen_at"] > 30)
        owner_records = []
        for owner in owners:
            record = dict(owner)
            record["heartbeat_age_seconds"] = max(0.0, now - owner["last_heartbeat_at"])
            record["heartbeat_fresh"] = record["heartbeat_age_seconds"] <= owner_stale_seconds
            owner_records.append(record)
        active_pages = [page for page in pages if page["status"] != "unavailable"]
        return {
            "reachable": True,
            "journal_mode": journal_mode,
            "registered_page_count": len(active_pages),
            "available_page_count": available,
            "stale_page_count": stale,
            "pages": [dict(page) for page in pages],
            "owners": owner_records,
            "fresh_owner_count": sum(1 for owner in owner_records if owner["heartbeat_fresh"] and owner["status"] == "healthy"),
            "owner_heartbeat_schema_missing": schema_missing,
        }
    except (OSError, sqlite3.Error) as exc:
        return {"reachable": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if connection is not None:
            connection.close()


def check_health(
    cdp_url: str = DEFAULT_CDP_URL,
    database: str = DEFAULT_DATABASE,
    *,
    timeout_seconds: float = 3.0,
    minimum_pages: int = 1,
    owner_stale_seconds: float = 15.0,
) -> Dict[str, Any]:
    cdp = _cdp_health(cdp_url, timeout_seconds)
    database_state = _database_health(database, timeout_seconds, owner_stale_seconds)
    cdp_ok = bool(cdp.get("reachable")) and int(cdp.get("page_count", 0)) >= minimum_pages
    database_ok = bool(database_state.get("reachable"))
    registered_ok = int(database_state.get("registered_page_count", 0)) >= minimum_pages if database_ok else False
    owner_fresh = int(database_state.get("fresh_owner_count", 0)) > 0
    if cdp_ok and database_ok and registered_ok and owner_fresh:
        status = "healthy"
    elif cdp.get("reachable") and database_ok:
        status = "degraded"
    else:
        status = "unavailable"
    return {
        "checked_at": _utc_now(),
        "status": status,
        "cdp": cdp,
        "database": database_state,
        "browser_use_session": "not_probed_by_http_health_check",
        "checks": {
            "cdp_reachable": bool(cdp.get("reachable")),
            "cdp_minimum_pages": cdp_ok,
            "database_reachable": database_ok,
            "database_minimum_pages": registered_ok,
            "browser_owner_heartbeat": owner_fresh,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Check local Chrome/CDP and browser-page health")
    parser.add_argument("--cdp-url", default=os.environ.get("MICROINDIA_CDP_URL", DEFAULT_CDP_URL))
    parser.add_argument("--database", default=os.environ.get("MICROINDIA_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--health-file", default=os.environ.get("MICROINDIA_HEALTH_FILE", DEFAULT_HEALTH_FILE))
    parser.add_argument("--timeout-seconds", type=float, default=3.0)
    parser.add_argument("--minimum-pages", type=int, default=1)
    parser.add_argument("--owner-stale-seconds", type=float, default=15.0)
    parser.add_argument("--check", action="store_true", help="Return exit code 1 unless status is healthy")
    args = parser.parse_args()
    result = check_health(
        args.cdp_url,
        args.database,
        timeout_seconds=args.timeout_seconds,
        minimum_pages=args.minimum_pages,
        owner_stale_seconds=args.owner_stale_seconds,
    )
    health_file = Path(args.health_file)
    health_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = health_file.with_suffix(health_file.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True))
    temporary.replace(health_file)
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.check and result["status"] != "healthy":
        raise SystemExit(1)


if __name__ == "__main__":
    main()