"""Speech to text on this Mac with mlx-whisper (large-v3-turbo).

Whisper invents text over music ("Thank you.", song lyrics loops), so every segment keeps the
model's own confidence and is marked ``speech`` only when it looks like real speech; the reel
prompt is told to trust only those segments.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List

MODEL = os.environ.get("MICROINDIA_WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
TRANSCRIPT_VERSION = "whisper-v2"  # v2: compression ratio alone no longer marks Indic-script speech as unsure
_LOCK = threading.Lock()  # one transcription at a time per process (GPU, and mlx is not thread-safe)

# A segment counts as speech when whisper is reasonably sure there is speech and of its words.
# Compression ratio flags repetition loops, but Indic scripts (3-byte UTF-8) compress well by nature,
# so a high ratio only counts against a segment whose words are also uncertain, or when it is extreme.
NO_SPEECH_MAX = 0.6
LOGPROB_MIN = -1.0
COMPRESSION_SOFT = 2.4
COMPRESSION_HARD = 3.2
LOGPROB_CONFIDENT = -0.4


def _is_speech(segment: Dict[str, Any]) -> bool:
    logprob = float(segment.get("avg_logprob") if segment.get("avg_logprob") is not None else -9.0)
    compression = float(segment.get("compression_ratio") or 0.0)
    if float(segment.get("no_speech_prob") or 0.0) >= NO_SPEECH_MAX or logprob <= LOGPROB_MIN:
        return False
    if compression >= COMPRESSION_HARD or (compression >= COMPRESSION_SOFT and logprob < LOGPROB_CONFIDENT):
        return False
    return bool(str(segment.get("text") or "").strip())


def reflag(transcript: Dict[str, Any]) -> Dict[str, Any]:
    """Re-apply the current speech rule to a cached transcript (the raw whisper scores are kept)."""
    segments = transcript.get("segments") or []
    if not segments:
        return transcript
    for segment in segments:
        segment["speech"] = _is_speech(segment)
    spoken = [segment for segment in segments if segment["speech"]]
    speech_seconds = sum(segment["end"] - segment["start"] for segment in spoken)
    transcript.update(text=" ".join(segment["text"] for segment in spoken).strip(), speech_detected=speech_seconds >= 1.5,
                      speech_seconds=round(speech_seconds, 2), version=TRANSCRIPT_VERSION)
    return transcript


def shape(result: Dict[str, Any], *, model: str = MODEL, seconds: float = 0.0) -> Dict[str, Any]:
    segments: List[Dict[str, Any]] = []
    for segment in result.get("segments") or []:
        segments.append({
            "start": round(float(segment.get("start") or 0.0), 2),
            "end": round(float(segment.get("end") or 0.0), 2),
            "text": str(segment.get("text") or "").strip(),
            "no_speech_prob": round(float(segment.get("no_speech_prob") or 0.0), 3),
            "avg_logprob": round(float(segment.get("avg_logprob") or 0.0), 3),
            "compression_ratio": round(float(segment.get("compression_ratio") or 0.0), 2),
            "speech": _is_speech(segment),
        })
    spoken = [segment for segment in segments if segment["speech"]]
    speech_seconds = sum(segment["end"] - segment["start"] for segment in spoken)
    return {
        "version": TRANSCRIPT_VERSION,
        "model": model,
        "language": result.get("language"),
        "text": " ".join(segment["text"] for segment in spoken).strip(),
        "raw_text": str(result.get("text") or "").strip(),
        "segments": segments,
        "speech_detected": speech_seconds >= 1.5,
        "speech_seconds": round(speech_seconds, 2),
        "seconds": round(seconds, 2),
    }


def transcribe(wav_path: str, *, model: str = MODEL) -> Dict[str, Any]:
    import mlx_whisper  # heavy: only the analyzer imports it

    started = time.time()
    with _LOCK:
        result = mlx_whisper.transcribe(
            wav_path,
            path_or_hf_repo=model,
            condition_on_previous_text=False,  # stops repetition loops over music
            verbose=None,
        )
    return shape(result, model=model, seconds=time.time() - started)


def save(transcript: Dict[str, Any], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        json.dump(transcript, handle, ensure_ascii=False, indent=1)


def load(path: str) -> Dict[str, Any]:
    with open(path) as handle:
        return reflag(json.load(handle))


def silent(reason: str) -> Dict[str, Any]:
    return {"version": TRANSCRIPT_VERSION, "model": None, "language": None, "text": "", "raw_text": "", "segments": [],
            "speech_detected": False, "speech_seconds": 0.0, "seconds": 0.0, "note": reason}
