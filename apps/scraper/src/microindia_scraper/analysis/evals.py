"""Score reel analyses and dossiers against hand-checked golden sets, and compare with the previous run.

Golden sets live in ``data/evals/golden_<set>.json`` (built by the data-quality agent)::

    {"version": 1, "items": [
      {"shortcode": "DdMJKW6SQGE", "note": "Tide demo, Tamil voice", "expected": {
         "format.primary": "product_demo",            # string: equal (case-insensitive); or {"any_of": [...]}
         "topic.niche": "home & decor",
         "audio.kind": "speech",
         "audio.spoken_languages": ["Tamil"],         # list: F1 over names (language/name/brand/niche/flag)
         "sponsorship.disclosed": true,               # bool: equal
         "sponsorship.detected": true,
         "face_to_camera.value": true,
         "production.quality": 3,                     # int: within 1 counts as right
         "products_brands": ["Tide"]}}]}

    golden_creators.json items use "handle" and dossier paths, e.g. "category_fit.top": "food" (the first
    ranked niche must match; any_of allowed), "voice.languages": ["Hindi", "English"],
    "brands.worked_with": ["Tide"], "brand_safety.rating": "safe", "production_level.score": 3.

Each run writes ``data/evals/runs/<UTC timestamp>.json`` with per-field accuracy, null rate and the
invented-evidence rate (references to frames/times/reels that don't exist), then prints the change
against the previous run and every field that dropped by more than 5 points with two example rows.
Scoring never opens a browser: a golden reel without a current analysis is analysed only when its
frames are cached, otherwise it is reported as missing.
"""

from __future__ import annotations

import glob
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from . import db
from .spec import load

SETS = ("reels", "creators", "businesses")
MIN_ROWS = 30
REGRESSION_POINTS = 5.0


def evals_dir(database: str) -> str:
    return os.environ.get("MICROINDIA_EVALS_DIR") or os.path.join(os.path.dirname(os.path.abspath(database)), "evals")


# -- field access and scoring -------------------------------------------------------------

_NAME_KEYS = ("language", "name", "brand", "niche", "flag", "format", "angle", "point")


def _names(value: Any) -> List[str]:
    out = []
    for item in value or []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            for key in _NAME_KEYS:
                if isinstance(item.get(key), str):
                    out.append(item[key])
                    break
    return out


def get_path(result: Any, path: str) -> Any:
    value = result
    for part in path.split("."):
        if part == "top":
            names = _names(value) if isinstance(value, list) else []
            return names[0] if names else None
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _norm(value: Any) -> str:
    return " ".join(str(value).lower().replace("_", " ").split())


def score_field(expected: Any, actual: Any) -> Optional[float]:
    """1.0 right, 0.0 wrong, partial for lists; None when the expectation can't be scored."""
    if isinstance(expected, dict) and "any_of" in expected:
        return max((score_field(option, actual) or 0.0) for option in expected["any_of"])
    if isinstance(expected, bool):
        return 1.0 if actual is expected else 0.0
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if not isinstance(actual, (int, float)) or isinstance(actual, bool):
            return 0.0
        return 1.0 if abs(actual - expected) <= 1 else 0.0
    if isinstance(expected, list):
        want = {_norm(item) for item in expected}
        got = {_norm(item) for item in (_names(actual) if isinstance(actual, list) else [])}
        if not want and not got:
            return 1.0
        if not want or not got:
            return 0.0
        hits = sum(1 for item in want if any(item in other or other in item for other in got))
        precision = sum(1 for item in got if any(item in other or other in item for other in want)) / len(got)
        recall = hits / len(want)
        return 0.0 if precision + recall == 0 else round(2 * precision * recall / (precision + recall), 3)
    if expected is None:
        return 1.0 if actual in (None, [], "") else 0.0
    return 1.0 if _norm(expected) == _norm(actual) else 0.0


def null_rate(result: Any) -> Tuple[int, int]:
    """(null leaves, all leaves) over scalar fields."""
    nulls = total = 0
    if isinstance(result, dict):
        for key, value in result.items():
            if key in ("evidence", "unknowns"):
                continue
            n, t = null_rate(value)
            nulls, total = nulls + n, total + t
    elif isinstance(result, list):
        for value in result:
            n, t = null_rate(value)
            nulls, total = nulls + n, total + t
    else:
        total = 1
        nulls = 1 if result is None else 0
    return nulls, total


# -- loading ---------------------------------------------------------------------------

def load_golden(database: str, name: str) -> Optional[Dict[str, Any]]:
    path = os.path.join(evals_dir(database), f"golden_{name}.json")
    if not os.path.exists(path):
        return None
    with open(path) as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {"items": data}


def _reel_result(database: str, shortcode: str, *, run_missing: bool) -> Tuple[Optional[Dict[str, Any]], str]:
    from .reel import MissingInput, analyze

    connection = db.connect(database)
    try:
        row = db.latest_analysis(connection, shortcode, load("reel").version)
        assets = db.get_assets(connection, shortcode)
    finally:
        connection.close()
    if row:
        return row["result"], "ok"
    if not run_missing or not assets or not assets.get("frames_json"):
        return None, "missing (no current analysis; run /analyze-reel first)"
    try:
        return analyze(database, shortcode)["result"], "analysed"
    except MissingInput as exc:
        return None, f"missing ({exc})"


def _creator_result(database: str, handle: str) -> Tuple[Optional[Dict[str, Any]], str]:
    connection = db.connect(database)
    try:
        row = db.latest_dossier(connection, handle.lower().lstrip("@"))
    finally:
        connection.close()
    if row and row["dossier_version"] == load("creator").version:
        return row["result"], "ok"
    return None, "missing (no current dossier; run /creator-dossier first)"


def score_set(database: str, name: str, *, run_missing: bool = True) -> Dict[str, Any]:
    golden = load_golden(database, name)
    if golden is None:
        return {"status": "no_golden_set", "message": f"No golden set at {os.path.join(evals_dir(database), f'golden_{name}.json')}. "
                "Ask the data-quality agent to build one (30+ hand-checked rows)."}
    if name == "businesses":
        return {"status": "not_built", "message": "Business cards are Phase 5 (Local finder); nothing to score yet."}
    items = golden.get("items") or []
    fields: Dict[str, List[float]] = {}
    rows = []
    for item in items:
        if name == "reels":
            key = item.get("shortcode")
            result, status = _reel_result(database, key, run_missing=run_missing)
        else:
            key = item.get("handle")
            result, status = _creator_result(database, key)
        row = {"key": key, "status": status, "fields": {}}
        if result is not None:
            for path, expected in (item.get("expected") or {}).items():
                actual = get_path(result, path)
                score = score_field(expected, actual)
                if score is None:
                    continue
                fields.setdefault(path, []).append(score)
                row["fields"][path] = {"score": score, "expected": expected, "actual": actual}
        rows.append(row)
    accuracy = {path: round(100 * sum(values) / len(values), 1) for path, values in fields.items() if values}
    scored = [value for values in fields.values() for value in values]
    return {
        "status": "ok",
        "rows": len(items),
        "scored_rows": sum(1 for row in rows if row["fields"]),
        "missing": [row["key"] for row in rows if not row["fields"]],
        "small_set": len(items) < MIN_ROWS,
        "overall": round(100 * sum(scored) / len(scored), 1) if scored else None,
        "fields": accuracy,
        "row_details": rows,
    }


def intrinsic(database: str) -> Dict[str, Any]:
    """Quality signals that need no golden set: null rate and references to evidence that doesn't exist."""
    from . import transcribe
    from .creator import dossier_problems
    from .reel import evidence_problems

    reel_version, creator_version = load("reel").version, load("creator").version
    connection = db.connect(database)
    try:
        analyses = connection.execute(
            """SELECT shortcode, result_json FROM reel_analyses a WHERE analysis_version = ? AND analysis_id = (
                   SELECT MAX(analysis_id) FROM reel_analyses b WHERE b.shortcode = a.shortcode AND b.analysis_version = a.analysis_version)""",
            (reel_version,),
        ).fetchall()
        nulls = total = refs = invalid = 0
        examples: List[Dict[str, Any]] = []
        for shortcode, payload in analyses:
            result = json.loads(payload)
            n, t = null_rate(result)
            nulls, total = nulls + n, total + t
            assets = db.get_assets(connection, shortcode) or {}
            media = db.latest_media(connection, shortcode) or {}
            path = assets.get("transcript_path")
            transcript = transcribe.load(path) if path and os.path.exists(path) else {"segments": []}
            check = evidence_problems(result, assets, transcript, media.get("duration"))
            refs += check["refs"]
            invalid += len(check["invalid"])
            if check["invalid"] and len(examples) < 5:
                examples.append({"shortcode": shortcode, "invalid": check["invalid"][:5]})
        dossiers = connection.execute(
            """SELECT creator, reel_set, result_json FROM creator_dossiers d WHERE dossier_version = ? AND dossier_id = (
                   SELECT MAX(dossier_id) FROM creator_dossiers e WHERE e.creator = d.creator AND e.dossier_version = d.dossier_version)""",
            (creator_version,),
        ).fetchall()
        citations = bad_citations = 0
        for creator, reel_set, payload in dossiers:
            check = dossier_problems(json.loads(payload), json.loads(reel_set))
            citations += check["citations"]
            bad_citations += len(check["invalid"])
    finally:
        connection.close()
    return {
        "reel_analyses": len(analyses),
        "reel_null_rate": round(nulls / total, 3) if total else None,
        "reel_invalid_evidence_rate": round(invalid / refs, 4) if refs else None,
        "reel_invalid_examples": examples,
        "dossiers": len(dossiers),
        "dossier_invalid_citation_rate": round(bad_citations / citations, 4) if citations else None,
    }


def previous_run(directory: str) -> Optional[Dict[str, Any]]:
    runs = sorted(glob.glob(os.path.join(directory, "*.json")))
    if not runs:
        return None
    with open(runs[-1]) as handle:
        return json.load(handle)


def compare(current: Dict[str, Any], before: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not before:
        return {"previous": None}
    changes: Dict[str, Any] = {"previous": before.get("ts"), "sets": {}}
    for name, result in (current.get("sets") or {}).items():
        old = ((before.get("sets") or {}).get(name) or {})
        if result.get("status") != "ok" or old.get("status") != "ok":
            continue
        deltas = {path: round(score - old["fields"][path], 1) for path, score in result["fields"].items() if path in (old.get("fields") or {})}
        regressions = []
        for path, delta in deltas.items():
            if delta < -REGRESSION_POINTS:
                examples = [{"key": row["key"], **row["fields"][path]} for row in result["row_details"]
                            if path in row["fields"] and row["fields"][path]["score"] < 1][:2]
                regressions.append({"field": path, "delta": delta, "examples": examples})
        changes["sets"][name] = {
            "overall_delta": round(result["overall"] - old["overall"], 1) if result.get("overall") is not None and old.get("overall") is not None else None,
            "field_deltas": deltas,
            "regressions": regressions,
        }
    return changes


def run(database: str, which: str = "all", *, run_missing: bool = True) -> Dict[str, Any]:
    names = SETS if which == "all" else (which,)
    if any(name not in SETS for name in names):
        raise ValueError(f"unknown set {which!r}; use one of {', '.join(SETS)} or all")
    directory = os.path.join(evals_dir(database), "runs")
    before = previous_run(directory)
    current = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "prompt_versions": {"reel": load("reel").version, "creator": load("creator").version},
        "prompt_hashes": {"reel": load("reel").prompt_hash, "creator": load("creator").prompt_hash},
        "sets": {name: score_set(database, name, run_missing=run_missing) for name in names},
        "intrinsic": intrinsic(database),
    }
    current["comparison"] = compare(current, before)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + ".json")
    with open(path, "w") as handle:
        json.dump(current, handle, ensure_ascii=False, indent=1, default=str)
    current["written_to"] = path
    return current
