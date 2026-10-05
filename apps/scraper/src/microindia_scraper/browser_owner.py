"""Keep one shared Chrome/CDP session populated with leaseable tabs."""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import uuid

from browser_use import Browser

from .store import CaptureStore


def _target_id(page: object) -> str:
    value = getattr(page, "target_id", None) or getattr(page, "_target_id", None)
    if not value:
        raise RuntimeError("Browser Use page did not expose a target ID")
    return str(value)


async def sync_pages(browser: Browser, store: CaptureStore, owner_id: str, minimum_pages: int, cdp_url: str = "") -> int:
    pages = await browser.get_pages()
    while len(pages) < minimum_pages:
        pages.append(await browser.new_page())
    target_ids = [_target_id(page) for page in pages]
    for target_id in target_ids:
        store.register_browser_page(target_id, owner_id)
    store.mark_missing_browser_pages(owner_id, target_ids)
    store.heartbeat_browser_owner(
        owner_id,
        status="healthy",
        cdp_url=cdp_url,
        minimum_pages=minimum_pages,
        page_count=len(target_ids),
    )
    return len(target_ids)


async def run(cdp_url: str, database: str, owner_id: str, minimum_pages: int, poll_seconds: float) -> None:
    browser = Browser(cdp_url=cdp_url)
    store = CaptureStore(database)
    try:
        await browser.start()
        while True:
            count = await sync_pages(browser, store, owner_id, minimum_pages, cdp_url)
            print(f"browser owner {owner_id}: {count} page(s) registered", flush=True)
            await asyncio.sleep(poll_seconds)
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Maintain leaseable tabs in one shared Chrome/CDP session")
    parser.add_argument("--cdp-url", default=os.environ.get("MICROINDIA_CDP_URL", "http://127.0.0.1:9222"))
    parser.add_argument("--database", default=os.environ.get("MICROINDIA_DATABASE", "data/microindia.sqlite3"))
    parser.add_argument("--owner-id", default=f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    parser.add_argument("--min-pages", type=int, default=2)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if args.min_pages < 1:
        parser.error("--min-pages must be at least 1")
    try:
        asyncio.run(run(args.cdp_url, args.database, args.owner_id, args.min_pages, args.poll_seconds))
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()