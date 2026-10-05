"""Reel media on disk: shortcode <-> media pk, paths under ``data/media/``, and ffmpeg extraction.

Layout (everything but the mp4 is kept for re-analysis):

    data/media/video/<shortcode>.mp4                 deleted after analysis
    data/media/frames/<shortcode>/H0..H3.jpg         hook frames at 0, 1, 2, 3 s
    data/media/frames/<shortcode>/K1..K8.jpg         scene keyframes (max 8, longest side 768 px)
    data/media/audio/<shortcode>/audio.opus          compressed audio (mono, 32 kbps)
    data/media/transcripts/<shortcode>/transcript.json
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from ..shortcodes import (  # noqa: F401  (re-exported: analysis code imports them from here)
    ALPHABET, INSTAGRAM_EPOCH_MS, pk_to_shortcode, shortcode_of, shortcode_timestamp, shortcode_to_pk,
)

MAX_KEYFRAMES = 8
FRAME_SIZE = 768
HOOK_SECONDS = (0.0, 1.0, 2.0, 3.0)
SCENE_THRESHOLD = 0.27


def owner_of_permalink(permalink: str) -> Optional[str]:
    """``instagram.com/<owner>/reel/<code>/`` names the owner; ``/reel/<code>/`` doesn't."""
    match = re.search(r"instagram\.com/([A-Za-z0-9._]+)/(?:reel|reels|p|tv)/", permalink or "")
    return match.group(1).lower() if match else None


# -- paths -------------------------------------------------------------------------

def media_root(database: str) -> str:
    return os.environ.get("MICROINDIA_MEDIA_DIR") or os.path.join(os.path.dirname(os.path.abspath(database)), "media")


def paths_for(database: str, shortcode: str) -> Dict[str, str]:
    root = media_root(database)
    return {
        "video": os.path.join(root, "video", f"{shortcode}.mp4"),
        "frames": os.path.join(root, "frames", shortcode),
        "audio_dir": os.path.join(root, "audio", shortcode),
        "opus": os.path.join(root, "audio", shortcode, "audio.opus"),
        "transcripts": os.path.join(root, "transcripts", shortcode),
        "transcript": os.path.join(root, "transcripts", shortcode, "transcript.json"),
    }


# -- ffmpeg ----------------------------------------------------------------------------

def ffmpeg_binary(name: str = "ffmpeg") -> str:
    found = shutil.which(name) or (f"/opt/homebrew/bin/{name}" if os.path.exists(f"/opt/homebrew/bin/{name}") else None)
    if not found:
        raise RuntimeError(f"{name} not found: brew install ffmpeg")
    return found


def _run(argv: List[str], timeout: float = 300) -> subprocess.CompletedProcess:
    completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if completed.returncode != 0:
        raise RuntimeError(f"{os.path.basename(argv[0])} failed: {completed.stderr[-500:]}")
    return completed


def probe(video: str) -> Dict[str, Any]:
    out = _run([ffmpeg_binary("ffprobe"), "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                "-of", "default=noprint_wrappers=1", video]).stdout
    duration = re.search(r"duration=([\d.]+)", out)
    width = re.search(r"width=(\d+)", out)
    height = re.search(r"height=(\d+)", out)
    return {
        "duration": float(duration.group(1)) if duration else None,
        "width": int(width.group(1)) if width else None,
        "height": int(height.group(1)) if height else None,
        "has_audio": "codec_type=audio" in out,
    }


def scene_changes(video: str, threshold: float = SCENE_THRESHOLD) -> List[float]:
    """Timestamps where the picture changes (cuts), from ffmpeg's scene score."""
    completed = subprocess.run(
        [ffmpeg_binary(), "-hide_banner", "-nostats", "-i", video, "-an", "-vf",
         f"scale=160:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True, timeout=300,
    )
    return sorted({round(float(t), 2) for t in re.findall(r"pts_time:([\d.]+)", completed.stderr)})


def choose_keyframe_times(duration: float, scenes: List[float], limit: int = MAX_KEYFRAMES) -> List[float]:
    """Up to ``limit`` moments that cover the reel: just after each cut, gaps filled evenly.

    The first 3 s are covered by the hook frames, so keyframes start after them when the reel is long enough.
    """
    if not duration or duration <= 0:
        return []
    start = 3.2 if duration > 6 else 0.0
    end = max(start, duration - 0.15)
    candidates = [min(end, t + 0.35) for t in scenes if start <= t + 0.35 <= end]
    if len(candidates) > limit:  # many cuts: keep an even spread
        step = len(candidates) / limit
        candidates = [candidates[int(i * step)] for i in range(limit)]
    picked = sorted(set(round(t, 2) for t in candidates))
    # Fill the largest gaps until we have enough (talking heads have few cuts).
    wanted = min(limit, max(3, int(duration // 4) + 1))
    while len(picked) < wanted:
        points = [start] + picked + [end]
        gaps = [(points[i + 1] - points[i], i) for i in range(len(points) - 1)]
        size, index = max(gaps)
        if size < 1.0:
            break
        picked = sorted(set(picked + [round(points[index] + size / 2, 2)]))
    return picked[:limit]


def _scale_filter(size: int = FRAME_SIZE) -> str:
    return f"scale='if(gt(iw,ih),{size},-2)':'if(gt(iw,ih),-2,{size})'"


def extract_frame(video: str, seconds: float, out_path: str) -> None:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    _run([ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{seconds:.2f}", "-i", video,
          "-frames:v", "1", "-vf", _scale_filter(), "-q:v", "3", out_path])


def extract_audio(video: str, wav_path: str, opus_path: str) -> None:
    """16 kHz mono wav for whisper (temporary) and a small opus copy to keep."""
    os.makedirs(os.path.dirname(opus_path), exist_ok=True)
    _run([ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-y", "-i", video, "-vn",
          "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav_path,
          "-vn", "-ac", "1", "-c:a", "libopus", "-b:a", "32k", opus_path])


def opus_to_wav(opus_path: str, wav_path: str) -> None:
    _run([ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-y", "-i", opus_path, "-ac", "1", "-ar", "16000",
          "-c:a", "pcm_s16le", wav_path])


def extract_all(video: str, frames_dir: str, opus_path: str, wav_path: str) -> Dict[str, Any]:
    """Hook frames, scene keyframes and audio for one reel. Returns what was made, with timings."""
    import time

    started = time.time()
    info = probe(video)
    duration = info["duration"] or 0.0
    os.makedirs(frames_dir, exist_ok=True)
    hook = []
    for second in HOOK_SECONDS:
        if duration and second > duration - 0.05:
            continue
        path = os.path.join(frames_dir, f"H{int(second)}.jpg")
        extract_frame(video, second if second else 0.05, path)
        hook.append({"id": f"H{int(second)}", "t": second, "path": path})
    scenes = scene_changes(video)
    keyframes = []
    for index, second in enumerate(choose_keyframe_times(duration, scenes), start=1):
        path = os.path.join(frames_dir, f"K{index}.jpg")
        extract_frame(video, second, path)
        keyframes.append({"id": f"K{index}", "t": second, "path": path})
    frames_seconds = time.time() - started
    audio = False
    if info["has_audio"]:
        extract_audio(video, wav_path, opus_path)
        audio = True
    return {
        "duration": duration,
        "width": info["width"],
        "height": info["height"],
        "has_audio": audio,
        "scene_changes": scenes,
        "hook_frames": hook,
        "keyframes": keyframes,
        "seconds": {"frames": round(frames_seconds, 2), "audio": round(time.time() - started - frames_seconds, 2)},
    }


def temp_wav() -> Tuple[str, str]:
    directory = tempfile.mkdtemp(prefix="microindia-wav-")
    return directory, os.path.join(directory, "audio16k.wav")
