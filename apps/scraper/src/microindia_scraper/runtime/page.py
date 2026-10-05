"""Page helpers. Handlers touch the browser only through these."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, Optional

from .results import AuthRequired

INSTAGRAM_ORIGIN = "https://www.instagram.com"
# The web app's own app id; the JSON endpoints expect it next to the session cookies.
INSTAGRAM_APP_ID = "936619743392459"

_AUTH_URL_RE = re.compile(r"/accounts/login|/challenge|/checkpoint|/accounts/suspended", re.I)
_AUTH_BODY_RE = re.compile(r"login_required|checkpoint_required|challenge_required|log\s*in\s+to\s+instagram", re.I)


def is_auth_url(url: str) -> bool:
    return bool(_AUTH_URL_RE.search(url or ""))


async def evaluate(page: Any, script: str, *args: Any) -> Any:
    """Run JS in the page; browser_use returns strings, so decode JSON when it is JSON."""
    result = await page.evaluate(script, *args)
    if not isinstance(result, str) or not result:
        return result
    try:
        return json.loads(result)
    except json.JSONDecodeError:
        return result


async def current_url(page: Any) -> str:
    getter = getattr(page, "get_url", None)
    if getter is not None:
        try:
            return str(await getter() or "")
        except Exception:
            pass
    return str(getattr(page, "url", "") or "")


async def open_url(page: Any, url: str, *, settle_seconds: float = 1.5) -> str:
    """Navigate and wait briefly. Raises AuthRequired if Instagram redirects to login/challenge."""
    await page.goto(url)
    if settle_seconds:
        await asyncio.sleep(settle_seconds)
    landed = await current_url(page)
    if is_auth_url(landed):
        raise AuthRequired(f"redirected to {landed}")
    return landed


async def ensure_origin(page: Any, origin: str = INSTAGRAM_ORIGIN) -> None:
    """Same-origin fetches carry the session cookies only from a page on that origin."""
    if not (await current_url(page)).startswith(origin):
        await open_url(page, origin + "/")


async def fetch_json(page: Any, url: str, *, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """``fetch`` from inside the page with the signed-in cookies.

    Returns ``{"status", "url", "data", "text"}``; ``data`` is the parsed JSON or None.
    Raises AuthRequired on a login/challenge response.
    """
    await ensure_origin(page)
    pacer = getattr(page, "pacer", None)
    if pacer is not None:
        await pacer.before()
    request_headers = {"X-IG-App-ID": INSTAGRAM_APP_ID, "X-Requested-With": "XMLHttpRequest", **(headers or {})}
    result = await evaluate(
        page,
        f"""() => fetch({json.dumps(url)}, {{credentials: 'include', headers: {json.dumps(request_headers)}}})
          .then(async response => {{
            const text = await response.text();
            let data = null;
            try {{ data = JSON.parse(text); }} catch (_) {{}}
            return JSON.stringify({{status: response.status, url: response.url, data, text: data ? '' : text.slice(0, 4000)}});
          }})""",
    )
    if isinstance(result, str):
        result = {"status": 0, "url": url, "data": None, "text": result[:4000]}
    if not isinstance(result, dict):
        result = {"status": 0, "url": url, "data": None, "text": ""}
    status = int(result.get("status") or 0)
    if pacer is not None:
        pacer.record(status)
    if status == 429:
        from .pacing import Throttled

        raise Throttled(f"429 from {url}")
    marker = json.dumps(result.get("data")) if result.get("data") is not None else str(result.get("text") or "")
    if is_auth_url(str(result.get("url") or "")) or (status in (401, 403) and _AUTH_BODY_RE.search(marker)):
        raise AuthRequired(f"{status} from {url}")
    if isinstance(result.get("data"), dict) and _AUTH_BODY_RE.search(str(result["data"].get("message") or "")):
        raise AuthRequired(f"{result['data'].get('message')} from {url}")
    return result


async def page_shows_login(page: Any) -> bool:
    """True when the current page is a login/challenge wall."""
    if is_auth_url(await current_url(page)):
        return True
    body = await evaluate(page, "() => (document.body && document.body.innerText || '').slice(0, 4000)")
    has_login_form = await evaluate(page, "() => !!document.querySelector('input[name=\"password\"]')")
    return bool(has_login_form) or bool(_AUTH_BODY_RE.search(str(body or "")))
