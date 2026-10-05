"""The insight engine: reels -> analyses -> creator dossiers.

Importing this package registers its task kinds with the runtime:
- ``media.reel`` (browser; ``fetch.py``): media JSON + mp4 download;
- ``analyze.reel`` (no browser; ``reel.py``): ffmpeg frames + mlx-whisper + Claude Sonnet;
- ``analyze.creator`` (no browser; ``creator.py``): Claude Opus dossier.

Heavy dependencies (mlx-whisper, ffmpeg) load only when a reel is actually analysed.
CLI: ``python -m microindia_scraper.analysis reel|creator|eval|local-search``.
"""

from . import creator, fetch, reel  # noqa: F401  (registration side effects)
from .selection import reel_follow_ups, select_reels

__all__ = ["reel_follow_ups", "select_reels"]
