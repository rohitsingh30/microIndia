"""The old Find page's AI endpoints. Removed in B8 together with assistant.py and search_ai.chat_search."""

from __future__ import annotations

from typing import Any

from .router import Request, route


@route("POST", "/api/search/chat")
def search_chat(request: Request) -> Any:
    from ..search_ai import chat_search

    body = request.body
    answer = chat_search(str(body.get("message") or ""), body.get("criteria") or {})
    params = {**answer["criteria"], "sort": "engagement", "limit": "5"}
    answer["matches"] = request.repo.list_creators(params)["total"]
    return answer


@route("POST", "/api/assistant")
def assistant(request: Request) -> Any:
    from ..assistant import answer

    body = request.body
    return answer(request.repo, str(body.get("message") or ""), body.get("history") or [], body.get("filters") or {})
