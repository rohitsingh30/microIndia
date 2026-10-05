"""Click through the live dashboard in a real browser and report what breaks.

    ../scraper/.venv/bin/python qa.py [--url http://127.0.0.1:8787] [--out qa-shots]

Checks every page, live updates, search, filters, the creator drawer and the
command palette; fails on any console error, failed request or HTTP >= 400.
Screenshots land in --out for a visual pass.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Awaitable, Callable, List, Tuple

from playwright.async_api import Page, async_playwright


async def main(url: str, out: str) -> int:
    os.makedirs(out, exist_ok=True)
    results: List[Tuple[str, bool, str]] = []
    problems: List[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1366, "height": 860})
        page.on("console", lambda m: problems.append(f"console.{m.type}: {m.text[:160]}") if m.type == "error" else None)
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        page.on("response", lambda r: problems.append(f"HTTP {r.status} {r.url}") if r.status >= 400 else None)

        async def check(name: str, step: Callable[[Page], Awaitable[str]]) -> None:
            try:
                detail = await step(page)
                results.append((name, True, detail))
            except Exception as exc:  # keep going; report every failure
                results.append((name, False, f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"))

        async def live(page: Page) -> str:
            await page.goto(url)
            await page.wait_for_selector("text=Eligible creators", timeout=10_000)
            await page.wait_for_timeout(2500)
            await page.screenshot(path=f"{out}/live.png")
            status = await page.get_by_role("banner").inner_text()
            assert "Reconnecting" not in status, f"live stream not connected: {status!r}"
            return status.split("\n")[-3] if "\n" in status else status

        async def stream(page: Page) -> str:
            first = await page.locator("ol li").first.inner_text()
            await page.wait_for_timeout(15_000)
            latest = await page.locator("ol li").first.inner_text()
            return "feed advanced" if first != latest else "no new events in 15s (ok if idle)"

        async def creators(page: Page) -> str:
            await page.click("nav >> text=Creators")
            await page.wait_for_selector("tbody tr", timeout=10_000)
            rows = await page.locator("tbody tr").count()
            await page.screenshot(path=f"{out}/creators.png")
            return f"{rows} rows"

        async def search(page: Page) -> str:
            box = page.locator("input[placeholder^='Search handle']")
            await box.fill("food")
            await page.wait_for_timeout(1500)
            rows = await page.locator("tbody tr").count()
            await box.fill("")
            await page.wait_for_timeout(1000)
            assert rows > 0, "search for 'food' returned nothing"
            return f"'food' → {rows} rows"

        async def filters(page: Page) -> str:
            await page.click("text=Doing brand deals")
            await page.wait_for_timeout(1200)
            brand = await page.locator("tbody tr").count()
            await page.click("text=Doing brand deals")
            await page.click("button:has-text('Filters')")
            await page.wait_for_selector(".enter >> text=Min engagement", timeout=5000)
            await page.screenshot(path=f"{out}/filters.png")
            await page.click("button:has-text('Filters')")
            return f"brand deals → {brand} rows"

        async def creator_page(page: Page) -> str:
            await page.locator("tbody tr").first.click()
            await page.wait_for_url("**/creator/**", timeout=10_000)
            await page.wait_for_selector("text=What format works", timeout=10_000)
            await page.wait_for_timeout(1000)
            await page.screenshot(path=f"{out}/creator.png", full_page=True)
            body = await page.locator("main").inner_text()
            assert "[object Object]" not in body and "NaN" not in body, "creator page renders broken values"
            return page.url.rsplit("/", 1)[-1]

        async def find(page: Page) -> str:
            await page.click("nav >> text=Find")
            await page.evaluate("sessionStorage.removeItem('find-thread')")
            await page.goto(url + "/find")
            await page.wait_for_selector("text=Find the right creators", timeout=10_000)
            await page.screenshot(path=f"{out}/find-landing.png")
            await page.fill("textarea", "Launching a biryani masala in Hyderabad, need food creators under 50K")
            await page.keyboard.press("Enter")
            await page.wait_for_selector("text=Considered", timeout=60_000)
            await page.wait_for_timeout(1200)
            cards = await page.locator("article").count()
            await page.screenshot(path=f"{out}/find-results.png", full_page=True)
            assert cards > 0, "assistant returned no creators"
            await page.locator("article [aria-label=Shortlist]").first.click()
            await page.click("button:has-text('Shortlist')")
            await page.wait_for_selector("text=Your shortlist", timeout=5000)
            await page.locator("[role=dialog] [aria-label=Remove]").first.click()
            await page.keyboard.press("Escape")
            await page.fill("textarea", "which of these have done brand deals?")
            await page.keyboard.press("Enter")
            await page.wait_for_function("document.querySelectorAll('[class*=rounded-br-md]').length >= 2", timeout=30_000)
            await page.wait_for_timeout(2500)
            return f"{cards} recommended creators; follow-up answered; shortlist works"

        async def pipeline(page: Page) -> str:
            await page.click("nav >> text=Pipeline")
            await page.wait_for_selector("text=Runners", timeout=10_000)
            await page.wait_for_timeout(1500)
            await page.screenshot(path=f"{out}/pipeline.png", full_page=True)
            return f"{await page.locator('code').count()} task kinds/runners shown"

        async def system(page: Page) -> str:
            await page.click("nav >> text=System")
            await page.wait_for_selector("text=Profiles / hour", timeout=10_000)
            await page.wait_for_timeout(2000)
            await page.screenshot(path=f"{out}/system.png", full_page=True)
            body = await page.locator("main").inner_text()
            assert "NaN" not in body and "undefined" not in body, "system page renders broken values"
            return "speed, memory and cost panels render"

        async def palette(page: Page) -> str:
            await page.keyboard.press("Meta+k")
            await page.keyboard.type("food")
            await page.wait_for_selector("[cmdk-item]", timeout=5000)
            await page.wait_for_timeout(1200)
            items = await page.locator("[cmdk-item]").count()
            await page.screenshot(path=f"{out}/palette.png")
            await page.keyboard.press("Escape")
            return f"{items} items"

        for name, step in [("live page", live), ("live stream", stream), ("creators", creators), ("search", search),
                           ("filters", filters), ("creator page", creator_page), ("find", find), ("pipeline", pipeline), ("system", system), ("palette", palette)]:
            await check(name, step)
        await browser.close()

    width = max(len(name) for name, _, _ in results)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
    unique = list(dict.fromkeys(problems))
    for problem in unique:
        print(f"PROBLEM  {problem}")
    print(f"screenshots: {os.path.abspath(out)}")
    return 0 if all(ok for _, ok, _ in results) and not unique else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    parser.add_argument("--out", default="qa-shots")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.url, args.out)))
