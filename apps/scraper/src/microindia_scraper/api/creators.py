"""Creator routes: the searchable list (JSON or CSV) and one creator's detail."""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote

from .router import HttpError, Request, csv, route


@route("GET", "/api/creators")
def list_creators(request: Request) -> Any:
    result = request.repo.list_creators(request.params)
    if "csv" in result:
        return csv(result["csv"], "microindia-creators.csv")
    return result


# The handle is the last path segment, as before the split (/api/creators/x/y reads "y").
@route("GET", r"/api/creators/(?:.*/)?(?P<handle>[^/]*)")
def creator_detail(request: Request) -> Any:
    detail = request.repo.creator_detail(unquote(request.match["handle"]))
    if not detail:
        raise HttpError(404, "not found")
    return detail
