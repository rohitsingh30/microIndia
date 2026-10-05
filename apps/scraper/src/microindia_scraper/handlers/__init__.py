"""Task handlers. Importing this package registers every kind with the runtime."""

from . import scraping, sourcing  # noqa: F401  (registration side effects)
from .. import analysis  # noqa: F401  (registers media.reel, analyze.reel, analyze.creator)
from .scraping import refresh_eligible
from .sourcing import seed_searches

SCHEDULERS = [seed_searches, refresh_eligible]

__all__ = ["SCHEDULERS", "refresh_eligible", "seed_searches"]
