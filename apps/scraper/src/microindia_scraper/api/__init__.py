"""Dashboard API + live event stream + static SPA host (127.0.0.1 only).

    GET  /api/summary            KPIs, funnel, health, login state
    GET  /api/timeseries         found / scraped / eligible per hour (24h)
    GET  /api/activity?after=ts  recent finished tasks, newest first
    GET  /api/creators           search, filters, sort, paging (?format=csv)
    GET  /api/creators/<handle>  snapshot, metrics, posts, follower history, provenance
    GET  /api/pipeline           tasks by kind/state, runners, failure + skip reasons
    GET  /api/stats?hours=24     speed, Instagram requests, memory/CPU, AI cost
    GET  /api/sources            yield per source type and per seed
    GET  /api/events             Server-Sent Events: activity, summary, health
    POST /api/actions/unblock | retry | seed
    POST /api/search/chat, /api/assistant   (legacy Find page, removed in B8)

Layout: router.py (route decorator, Request, responses), server.py (HTTP handler, static files, CLI),
repository.py (creator index), ops.py (live-operations queries + routes), one module per route group.
"""

from ..profile_text import _india, _own_words  # noqa: F401  (handlers/scraping.py imports these from here)
from .ops import _reason_bucket
from .repository import in_band
from .server import DEFAULT_DIST, Handler, Repository, main, make_server, serve

__all__ = ["DEFAULT_DIST", "Handler", "Repository", "_india", "_own_words", "_reason_bucket", "in_band", "main",
           "make_server", "serve"]
