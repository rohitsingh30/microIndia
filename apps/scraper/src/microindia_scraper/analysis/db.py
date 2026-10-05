"""Insight-layer tables. Additive (``CREATE TABLE IF NOT EXISTS``); analyses and dossiers are append-only.

- ``reel_media``: one row per fetch of a reel's media JSON (metrics change, so rows are never updated).
- ``reel_assets``: where a reel's cached files live (keyframes, hook frames, opus, transcript), one row per reel.
- ``reel_analyses``: every model analysis of a reel, with prompt version/hash, input hash and raw output.
- ``creator_dossiers``: every dossier, with the reel set it was built from.
- ``llm_cache``: owned by ``llm.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional

from .. import llm


def connect(database: str) -> sqlite3.Connection:
    parent = os.path.dirname(database)
    if parent:
        os.makedirs(parent, exist_ok=True)
    connection = sqlite3.connect(database, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    ensure_schema(connection)
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS reel_media (
            media_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            shortcode TEXT NOT NULL,
            pk TEXT,
            owner_username TEXT,
            creator TEXT,
            taken_at TEXT,
            duration REAL,
            play_count INTEGER,
            ig_play_count INTEGER,
            view_count INTEGER,
            like_count INTEGER,
            comment_count INTEGER,
            has_audio INTEGER,
            audio_type TEXT,
            audio_json TEXT,
            cover_url TEXT,
            video_url TEXT,
            width INTEGER,
            height INTEGER,
            is_paid_partnership INTEGER,
            sponsor_tags TEXT,
            coauthors TEXT,
            usertags TEXT,
            location TEXT,
            caption TEXT,
            caption_language TEXT,
            raw_json TEXT,
            fetched_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS reel_media_shortcode ON reel_media(shortcode, fetched_at);
        CREATE INDEX IF NOT EXISTS reel_media_creator ON reel_media(creator);

        CREATE TABLE IF NOT EXISTS reel_assets (
            shortcode TEXT PRIMARY KEY,
            creator TEXT,
            video_path TEXT,
            video_bytes INTEGER,
            frames_json TEXT,
            hook_frames_json TEXT,
            audio_path TEXT,
            transcript_path TEXT,
            transcript_language TEXT,
            speech_detected INTEGER,
            seconds_json TEXT,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS reel_analyses (
            analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
            shortcode TEXT NOT NULL,
            creator TEXT,
            analysis_version TEXT NOT NULL,
            prompt_hash TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            model TEXT NOT NULL,
            result_json TEXT NOT NULL,
            raw TEXT,
            seconds REAL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS reel_analyses_shortcode ON reel_analyses(shortcode, created_at);
        CREATE INDEX IF NOT EXISTS reel_analyses_creator ON reel_analyses(creator, analysis_version);

        CREATE TABLE IF NOT EXISTS creator_dossiers (
            dossier_id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator TEXT NOT NULL,
            dossier_version TEXT NOT NULL,
            prompt_hash TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            reel_set_hash TEXT NOT NULL,
            reel_set TEXT NOT NULL,
            model TEXT NOT NULL,
            result_json TEXT NOT NULL,
            raw TEXT,
            seconds REAL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS creator_dossiers_creator ON creator_dossiers(creator, created_at);
        """
    )
    llm.ensure_cache(connection)
    connection.commit()


def _json(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True)


# -- reel_media ----------------------------------------------------------------------

MEDIA_COLUMNS = ("shortcode", "pk", "owner_username", "creator", "taken_at", "duration", "play_count", "ig_play_count",
                 "view_count", "like_count", "comment_count", "has_audio", "audio_type", "audio_json", "cover_url",
                 "video_url", "width", "height", "is_paid_partnership", "sponsor_tags", "coauthors", "usertags",
                 "location", "caption", "caption_language", "raw_json", "fetched_at")
JSON_MEDIA_COLUMNS = ("audio_json", "sponsor_tags", "coauthors", "usertags", "raw_json")


def save_media(connection: sqlite3.Connection, media: Dict[str, Any]) -> int:
    row = dict(media)
    row.setdefault("fetched_at", time.time())
    for column in JSON_MEDIA_COLUMNS:
        if column in row and not isinstance(row[column], (str, type(None))):
            row[column] = _json(row[column])
    for column in ("has_audio", "is_paid_partnership"):
        if isinstance(row.get(column), bool):
            row[column] = int(row[column])
    values = [row.get(column) for column in MEDIA_COLUMNS]
    cursor = connection.execute(
        f"INSERT INTO reel_media({', '.join(MEDIA_COLUMNS)}) VALUES ({', '.join('?' for _ in MEDIA_COLUMNS)})", values)
    connection.commit()
    return int(cursor.lastrowid)


def _media_row(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    for column in JSON_MEDIA_COLUMNS:
        if item.get(column):
            try:
                item[column] = json.loads(item[column])
            except (TypeError, ValueError):
                pass
    for column in ("has_audio", "is_paid_partnership"):
        if item.get(column) is not None:
            item[column] = bool(item[column])
    return item


def latest_media(connection: sqlite3.Connection, shortcode: str) -> Optional[Dict[str, Any]]:
    row = connection.execute(
        "SELECT * FROM reel_media WHERE shortcode = ? ORDER BY fetched_at DESC, media_row_id DESC LIMIT 1", (shortcode,)
    ).fetchone()
    return _media_row(row) if row else None


def creator_media(connection: sqlite3.Connection, creator: str) -> List[Dict[str, Any]]:
    """Latest media row per reel for a creator (without the raw JSON)."""
    rows = connection.execute(
        """SELECT * FROM reel_media m WHERE creator = ? AND media_row_id = (
               SELECT media_row_id FROM reel_media m2 WHERE m2.shortcode = m.shortcode
               ORDER BY fetched_at DESC, media_row_id DESC LIMIT 1)""",
        (creator,),
    ).fetchall()
    result = []
    for row in rows:
        item = _media_row(row)
        item.pop("raw_json", None)
        result.append(item)
    return result


# -- reel_assets -------------------------------------------------------------------

def save_assets(connection: sqlite3.Connection, shortcode: str, **fields: Any) -> None:
    current = get_assets(connection, shortcode) or {"shortcode": shortcode}
    current.update({k: v for k, v in fields.items()})
    current["updated_at"] = time.time()
    columns = ("shortcode", "creator", "video_path", "video_bytes", "frames_json", "hook_frames_json", "audio_path",
               "transcript_path", "transcript_language", "speech_detected", "seconds_json", "updated_at")
    values = []
    for column in columns:
        value = current.get(column)
        if column.endswith("_json") and not isinstance(value, (str, type(None))):
            value = _json(value)
        if isinstance(value, bool):
            value = int(value)
        values.append(value)
    connection.execute(
        f"""INSERT INTO reel_assets({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})
            ON CONFLICT(shortcode) DO UPDATE SET {', '.join(f'{c}=excluded.{c}' for c in columns[1:])}""",
        values,
    )
    connection.commit()


def get_assets(connection: sqlite3.Connection, shortcode: str) -> Optional[Dict[str, Any]]:
    row = connection.execute("SELECT * FROM reel_assets WHERE shortcode = ?", (shortcode,)).fetchone()
    if row is None:
        return None
    item = dict(row)
    for column in ("frames_json", "hook_frames_json", "seconds_json"):
        if item.get(column):
            item[column] = json.loads(item[column])
    return item


# -- reel_analyses -------------------------------------------------------------------

def save_analysis(connection: sqlite3.Connection, *, shortcode: str, creator: Optional[str], analysis_version: str,
                  prompt_hash: str, input_hash: str, model: str, result: Any, raw: Optional[str],
                  seconds: Optional[float]) -> int:
    existing = connection.execute(
        "SELECT analysis_id FROM reel_analyses WHERE shortcode = ? AND input_hash = ? AND analysis_version = ?",
        (shortcode, input_hash, analysis_version),
    ).fetchone()
    if existing:  # the very same input already produced a row: append-only, but no duplicates
        return int(existing[0])
    cursor = connection.execute(
        """INSERT INTO reel_analyses(shortcode, creator, analysis_version, prompt_hash, input_hash, model, result_json,
                                     raw, seconds, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (shortcode, creator, analysis_version, prompt_hash, input_hash, model, _json(result), raw, seconds, time.time()),
    )
    connection.commit()
    return int(cursor.lastrowid)


def latest_analysis(connection: sqlite3.Connection, shortcode: str, analysis_version: Optional[str] = None) -> Optional[Dict[str, Any]]:
    sql = "SELECT * FROM reel_analyses WHERE shortcode = ?"
    args: List[Any] = [shortcode]
    if analysis_version:
        sql += " AND analysis_version = ?"
        args.append(analysis_version)
    row = connection.execute(sql + " ORDER BY created_at DESC, analysis_id DESC LIMIT 1", args).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["result"] = json.loads(item.pop("result_json"))
    return item


def creator_analyses(connection: sqlite3.Connection, creator: str, analysis_version: str) -> List[Dict[str, Any]]:
    """Latest analysis per reel of the current version for a creator."""
    rows = connection.execute(
        """SELECT * FROM reel_analyses a WHERE creator = ? AND analysis_version = ? AND analysis_id = (
               SELECT analysis_id FROM reel_analyses a2 WHERE a2.shortcode = a.shortcode AND a2.analysis_version = a.analysis_version
               ORDER BY created_at DESC, analysis_id DESC LIMIT 1)
           ORDER BY shortcode""",
        (creator, analysis_version),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["result"] = json.loads(item.pop("result_json"))
        item.pop("raw", None)
        result.append(item)
    return result


# -- creator_dossiers -------------------------------------------------------------------

def reel_set_hash(shortcodes: Iterable[str], analysis_ids: Iterable[int] = ()) -> str:
    material = json.dumps({"reels": sorted(shortcodes), "analyses": sorted(analysis_ids)})
    return hashlib.sha256(material.encode()).hexdigest()


def save_dossier(connection: sqlite3.Connection, *, creator: str, dossier_version: str, prompt_hash: str,
                 input_hash: str, reel_set: List[str], set_hash: str, model: str, result: Any, raw: Optional[str],
                 seconds: Optional[float]) -> int:
    existing = connection.execute(
        "SELECT dossier_id FROM creator_dossiers WHERE creator = ? AND input_hash = ? AND dossier_version = ?",
        (creator, input_hash, dossier_version),
    ).fetchone()
    if existing:
        return int(existing[0])
    cursor = connection.execute(
        """INSERT INTO creator_dossiers(creator, dossier_version, prompt_hash, input_hash, reel_set_hash, reel_set, model,
                                        result_json, raw, seconds, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (creator, dossier_version, prompt_hash, input_hash, set_hash, _json(sorted(reel_set)), model, _json(result), raw,
         seconds, time.time()),
    )
    connection.commit()
    return int(cursor.lastrowid)


def latest_dossier(connection: sqlite3.Connection, creator: str) -> Optional[Dict[str, Any]]:
    row = connection.execute(
        "SELECT * FROM creator_dossiers WHERE creator = ? ORDER BY created_at DESC, dossier_id DESC LIMIT 1", (creator,)
    ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["result"] = json.loads(item.pop("result_json"))
    item["reel_set"] = json.loads(item["reel_set"])
    return item


def has_dossier_for(connection: sqlite3.Connection, creator: str, set_hash: str, dossier_version: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM creator_dossiers WHERE creator = ? AND reel_set_hash = ? AND dossier_version = ? LIMIT 1",
        (creator, set_hash, dossier_version),
    ).fetchone() is not None
