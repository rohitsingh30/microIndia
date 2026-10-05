"""The signed-in Chrome, attached over CDP with Playwright, plus the tabs one runner owns.

Playwright attaches in well under a second and only touches the tabs we open;
the signed-in cookies come from Chrome's default context.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from typing import Any, Dict, List, Optional


class Tab:
    """A runner-owned tab with the small surface handlers and the capture code use."""

    def __init__(self, page: Any, *, navigation_timeout_ms: int = 60_000, pacer: Any = None) -> None:
        self.page = page
        self.navigation_timeout_ms = navigation_timeout_ms
        self.pacer = pacer

    @property
    def target_id(self) -> str:
        return str(id(self.page))

    @property
    def url(self) -> str:
        return str(getattr(self.page, "url", "") or "")

    async def get_url(self) -> str:
        return self.url

    async def goto(self, url: str) -> None:
        if self.pacer is not None:
            await self.pacer.before()
        response = await self.page.goto(url, wait_until="domcontentloaded", timeout=self.navigation_timeout_ms)
        status = response.status if response is not None else 0
        if self.pacer is not None and "instagram.com" in url:
            self.pacer.record(status)
        if status == 429:
            from .pacing import Throttled

            raise Throttled(f"429 from {url}")

    async def evaluate(self, script: str, *args: Any) -> Any:
        if not args:
            return await self.page.evaluate(script)
        return await self.page.evaluate(script, args[0] if len(args) == 1 else list(args))

    def is_closed(self) -> bool:
        checker = getattr(self.page, "is_closed", None)
        return bool(checker()) if checker else False

    async def close(self) -> None:
        if not self.is_closed():
            await self.page.close()


HEAVY_RESOURCE_TYPES = {"image", "media", "font"}


async def _skip_heavy(route: Any) -> None:
    if route.request.resource_type in HEAVY_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.continue_()


def close_unresponsive_tabs(cdp_url: str, *, timeout: float = 8.0, skip: Optional[set] = None) -> List[str]:
    """Close page targets whose renderer no longer answers. One hung tab blocks every CDP attach."""
    import websockets  # shipped with the browser extra

    targets = json.load(urllib.request.urlopen(f"{cdp_url}/json/list", timeout=timeout))
    skip = skip or set()
    # Runner-owned tabs are busy by design; their runners recycle them. Only sweep strays.
    pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl") and t.get("id") not in skip]

    async def responsive(target: dict) -> bool:
        try:
            async with websockets.connect(target["webSocketDebuggerUrl"], max_size=None, open_timeout=timeout) as ws:
                await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": "1"}}))
                await asyncio.wait_for(ws.recv(), timeout)
                return True
        except Exception:
            return False

    async def sweep() -> List[str]:
        results = await asyncio.gather(*(responsive(target) for target in pages))
        suspects = [target for target, ok in zip(pages, results) if not ok]
        if not suspects:
            return []
        # Only a tab that is still silent on a second look is treated as hung.
        await asyncio.sleep(timeout)
        again = await asyncio.gather(*(responsive(target) for target in suspects))
        return [target["id"] for target, ok in zip(suspects, again) if not ok]

    hung = asyncio.run(sweep()) if not _loop_running() else []
    for target in hung:
        urllib.request.urlopen(f"{cdp_url}/json/close/{target}", timeout=timeout).read()
    return hung


def _loop_running() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


class BrowserSession:
    """Attach to the already-running signed-in Chrome; reconnect on demand."""

    def __init__(self, cdp_url: str, *, browser_factory: Any = None, connect_timeout: float = 30.0,
                 block_heavy_resources: bool = True, pacer: Any = None) -> None:
        self.cdp_url = cdp_url
        self.pacer = pacer
        self.block_heavy_resources = block_heavy_resources
        self._factory = browser_factory  # tests inject a fake with new_page/get_pages/close_page
        self.connect_timeout = connect_timeout
        self.browser: Any = None
        self._playwright: Any = None
        self._context: Any = None

    async def start(self) -> Any:
        if self._factory is not None:
            self.browser = self._factory()
            await self.browser.start()
            return self
        try:
            await self._attach()
        except Exception as first:
            # A wedged tab stalls attach; drop unresponsive tabs and try once more.
            await self.stop()
            hung = await asyncio.to_thread(close_unresponsive_tabs, self.cdp_url)
            if not hung:
                raise RuntimeError(f"could not attach to Chrome at {self.cdp_url}: {first}") from first
            print(json.dumps({"event": "closed_unresponsive_tabs", "targets": hung}), flush=True)
            await self._attach()
        return self

    async def _attach(self) -> None:
        from playwright.async_api import async_playwright  # lazy: stdlib-only modules load without it

        self._playwright = await async_playwright().start()
        self.browser = await self._playwright.chromium.connect_over_cdp(
            self.cdp_url, timeout=self.connect_timeout * 1000
        )
        if not self.browser.contexts:
            raise RuntimeError("Chrome has no default context; is the signed-in profile running?")
        self._context = self.browser.contexts[0]

    async def new_page(self) -> Tab:
        if self._factory is not None:
            return await self.browser.new_page()
        page = await self._context.new_page()
        if self.block_heavy_resources:
            # Only our own tabs: images/video/fonts are never parsed, so skip downloading them.
            await page.route("**/*", _skip_heavy)
        return Tab(page, pacer=self.pacer)

    async def close_page(self, tab: Any) -> None:
        if self._factory is not None:
            await self.browser.close_page(tab)
            return
        await tab.close()

    async def target_id_of(self, tab: Any) -> Optional[str]:
        """The tab's CDP target id (used to close leftovers from an earlier run)."""
        if self._factory is not None:
            return getattr(tab, "target_id", None)
        try:
            cdp = await self._context.new_cdp_session(tab.page)
            try:
                info = await cdp.send("Target.getTargetInfo")
            finally:
                await cdp.detach()
            return info["targetInfo"]["targetId"]
        except Exception:
            return None

    def is_closed(self, tab: Any) -> bool:
        if self._factory is not None:
            return tab not in getattr(self.browser, "pages", [tab])
        return tab.is_closed()

    async def ping(self) -> None:
        """Raise if the CDP connection is gone."""
        if self._factory is not None:
            await self.browser.get_pages()
            return
        if not self.browser.is_connected():
            raise RuntimeError("CDP connection closed")
        session = await self.browser.new_browser_cdp_session()
        try:
            await session.send("Browser.getVersion")
        finally:
            await session.detach()

    async def stop(self) -> None:
        if self._factory is not None:
            if self.browser is not None:
                await self.browser.stop()
            self.browser = None
            return
        # Stopping Playwright only disconnects; the signed-in Chrome keeps running.
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._playwright = None
        self.browser = None
        self._context = None


class TabPool:
    """N tabs owned by one runner. A tab that vanished is recreated, never a lost task.

    The pool records its tabs' CDP target ids in ``ledger``; a later start closes
    whatever an earlier (crashed or killed) run left open, so tabs never pile up.
    """

    def __init__(self, session: BrowserSession, size: int, *, ledger: Optional[str] = None, recycle_after: int = 25) -> None:
        if size < 1:
            raise ValueError("a tab pool needs at least one tab")
        self.session = session
        self.size = size
        self.ledger = ledger
        self.recycle_after = recycle_after  # long-lived Instagram tabs grow in memory; replace them periodically
        self._uses: Dict[int, int] = {}
        self._free: "asyncio.Queue[Any]" = asyncio.Queue()
        self._owned: List[Any] = []

    async def start(self) -> List[str]:
        closed = await asyncio.to_thread(self._close_leftovers)
        for _ in range(self.size):
            tab = await self.session.new_page()
            self._owned.append(tab)
            self._free.put_nowait(tab)
        await self._write_ledger()
        return closed

    def _close_leftovers(self) -> List[str]:
        if not self.ledger or not os.path.exists(self.ledger):
            return []
        try:
            leftover = json.load(open(self.ledger))
        except (OSError, ValueError):
            return []
        closed = []
        for target in leftover:
            try:
                urllib.request.urlopen(f"{self.session.cdp_url}/json/close/{target}", timeout=5).read()
                closed.append(target)
            except Exception:
                continue  # already gone
        return closed

    async def _write_ledger(self) -> None:
        if not self.ledger:
            return
        ids = [target for target in [await self.session.target_id_of(tab) for tab in self._owned] if target]
        os.makedirs(os.path.dirname(self.ledger) or ".", exist_ok=True)
        with open(self.ledger, "w") as handle:
            json.dump(ids, handle)

    async def acquire(self) -> Any:
        tab = await self._free.get()
        if self.session.is_closed(tab):
            fresh = await self.session.new_page()
            self._swap(tab, fresh)
            tab = fresh
        return tab

    def release(self, tab: Any) -> None:
        self._free.put_nowait(tab)

    async def finish(self, tab: Any) -> None:
        """Return a tab after a task; every ``recycle_after`` uses it is swapped for a fresh one."""
        uses = self._uses.get(id(tab), 0) + 1
        if self.recycle_after and uses >= self.recycle_after:
            self._uses.pop(id(tab), None)
            await self.replace(tab)
        else:
            self._uses[id(tab)] = uses
            self.release(tab)

    async def replace(self, tab: Any) -> None:
        """Close a misbehaving tab and put a fresh one back in the pool."""
        try:
            await self.session.close_page(tab)
        except Exception:
            pass
        fresh = await self.session.new_page()
        self._swap(tab, fresh)
        self._free.put_nowait(fresh)
        await self._write_ledger()

    def _swap(self, old: Any, new: Any) -> None:
        self._owned = [new if value is old else value for value in self._owned]

    async def stop(self) -> None:
        for tab in self._owned:
            if not self.session.is_closed(tab):
                try:
                    await self.session.close_page(tab)
                except Exception:
                    pass
        self._owned = []
        self._free = asyncio.Queue()
        if self.ledger and os.path.exists(self.ledger):
            os.remove(self.ledger)
