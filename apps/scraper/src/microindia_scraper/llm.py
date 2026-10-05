"""The only way microIndia calls a model: the ``claude`` CLI, headless.

    data, meta = llm.call(prompt, schema=REEL_SCHEMA, model="sonnet", images=[...], system=SYSTEM,
                          purpose="reel")

What it does for every call:
- **cache**: ``llm_cache`` (SQLite) keyed by sha256 of (model, system, prompt, schema, image bytes).
  An identical call is answered from the cache; ``cache=False`` forces a fresh run (and refreshes it).
- **concurrency**: at most ``MICROINDIA_LLM_SLOTS`` (default 2) calls at once across all processes,
  using ``flock`` on slot files next to the database (released by the OS if a process dies).
- **retry**: one retry on a timeout, a non-zero exit, an error envelope or output that isn't the schema's
  object (never cached). No retry when Claude is unavailable (limit, overload, signed out, network):
  that raises ``LLMUnavailable`` and starts a shared cooldown during which calls fail fast.
- **deadline**: callers pass the time they must be done by; waits and attempts are cut to fit, and the
  CLI runs in its own process group so a timeout kills it whole.
- **usage**: ``llm_calls``, ``llm_seconds``, ``llm_failures``, ``llm_cache_hits`` and ``llm_cost_usd``
  in the ``usage`` table, per hour (``YYYY-MM-DDTHH``) and per day (``YYYY-MM-DD``).

CLI facts this relies on (claude 2.1.x, checked 6 Oct 2026):
- ``--bare`` only authenticates with ANTHROPIC_API_KEY (never the subscription's OAuth), so it is used
  only when that key is set. Otherwise the call is kept minimal with ``--tools ""``,
  ``--setting-sources ""``, ``--strict-mcp-config``, ``--disable-slash-commands``,
  ``--no-session-persistence``, our own ``--system-prompt`` and a neutral temporary cwd.
- ``--output-format json`` prints one envelope: ``{"type": "result", "is_error", "subtype", "result"
  (text), "structured_output" (parsed object when --json-schema is given), "total_cost_usd", "usage",
  "modelUsage" {model id: tokens}}``.
- Images need ``--input-format stream-json``, which requires ``--output-format stream-json --verbose``;
  the envelope is then the last ``{"type": "result", ...}`` line.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

PACKAGE_ROOT = Path(__file__).resolve().parents[2]  # apps/scraper
DEFAULT_DATABASE = str(PACKAGE_ROOT / "data" / "microindia.sqlite3")
MODELS = {"sonnet": "sonnet", "opus": "opus", "haiku": "haiku"}


class LLMError(RuntimeError):
    """The model could not produce a usable answer (after the retry)."""


class LLMUnavailable(LLMError):
    """Claude can't be used right now (usage/rate limit, overloaded, signed out, network down).

    Never retried inside the call; it starts a shared cooldown (``runtime_flags.llm_cooldown_until``)
    during which every call fails fast. Handlers turn it into a refunded retry later: it is never
    the task's fault."""

    def __init__(self, message: str, *, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


COOLDOWN_FLAG = "llm_cooldown_until"
COOLDOWN_SECONDS = float(os.environ.get("MICROINDIA_LLM_COOLDOWN", "1200"))  # 20 min
MIN_ATTEMPT_SECONDS = 60.0  # don't start (or retry) a model call with less time than this left
_UNAVAILABLE_RE = re.compile(
    r"usage limit|rate.?limit|limit (?:reached|will reset)|too many requests|\b429\b|overloaded|\b529\b|"
    r"not logged in|please run /login|/login|authenticat|invalid api key|unauthori[sz]ed|\b401\b|oauth|"
    r"credit balance|ECONNREFUSED|ENOTFOUND|ECONNRESET|ETIMEDOUT|EAI_AGAIN|network error|connection error|"
    r"fetch failed|socket hang up|unable to connect",
    re.I,
)


def classify_failure(text: str, *, api_status: Any = None) -> bool:
    """True when a failure means Claude is unavailable (not that this prompt is bad)."""
    if api_status in (401, 403, 429, 529, "401", "403", "429", "529"):
        return True
    return bool(_UNAVAILABLE_RE.search(text or ""))


def database_path(database: Optional[str] = None) -> str:
    return database or os.environ.get("MICROINDIA_DATABASE") or DEFAULT_DATABASE


def claude_binary() -> Optional[str]:
    configured = os.environ.get("MICROINDIA_CLAUDE_BIN")
    if configured:
        return configured
    return shutil.which("claude") or ("/opt/homebrew/bin/claude" if os.path.exists("/opt/homebrew/bin/claude") else None)


def brand_ai_enabled() -> bool:
    """Brand-facing chat (search parsing, Ask-AI) uses the model only when switched on, so the API
    stays instant by default: MICROINDIA_LLM=on (or the legacy MICROINDIA_LLM_CMD being set)."""
    flag = os.environ.get("MICROINDIA_LLM", "").strip().lower()
    if flag in ("0", "off", "false", "no"):
        return False
    wanted = flag in ("1", "on", "true", "yes", "claude") or bool(os.environ.get("MICROINDIA_LLM_CMD", "").strip())
    return wanted and claude_binary() is not None


# -- storage -------------------------------------------------------------------

def ensure_cache(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS llm_cache (
            input_hash TEXT PRIMARY KEY,
            model TEXT NOT NULL,
            purpose TEXT,
            output_json TEXT NOT NULL,
            raw TEXT,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS usage (
            day TEXT NOT NULL,
            name TEXT NOT NULL,
            count REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (day, name)
        );
        """
    )


@contextmanager
def _db(database: str) -> Iterator[sqlite3.Connection]:
    parent = os.path.dirname(database)
    if parent:
        os.makedirs(parent, exist_ok=True)
    connection = sqlite3.connect(database, timeout=30)
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        ensure_cache(connection)
        yield connection
        connection.commit()
    finally:
        connection.close()


def _cache_get(database: str, input_hash: str) -> Optional[Dict[str, Any]]:
    with _db(database) as connection:
        row = connection.execute(
            "SELECT model, output_json, raw, created_at FROM llm_cache WHERE input_hash = ?", (input_hash,)
        ).fetchone()
    if row is None:
        return None
    return {"model": row[0], "output": json.loads(row[1]), "raw": row[2], "created_at": row[3]}


def _cache_put(database: str, input_hash: str, model: str, purpose: str, output: Any, raw: str) -> None:
    with _db(database) as connection:
        connection.execute(
            """INSERT INTO llm_cache(input_hash, model, purpose, output_json, raw, created_at) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(input_hash) DO UPDATE SET model=excluded.model, purpose=excluded.purpose,
                 output_json=excluded.output_json, raw=excluded.raw, created_at=excluded.created_at""",
            (input_hash, model, purpose, json.dumps(output, ensure_ascii=False, sort_keys=True), raw, time.time()),
        )


def count_usage(database: str, amounts: Dict[str, float], *, now: Optional[float] = None) -> None:
    """Add to hourly and daily usage counters. Never raises: accounting must not break a call."""
    current = time.time() if now is None else now
    keys = (time.strftime("%Y-%m-%dT%H", time.localtime(current)), time.strftime("%Y-%m-%d", time.localtime(current)))
    try:
        with _db(database) as connection:
            for key in keys:
                for name, amount in amounts.items():
                    if not amount:
                        continue
                    connection.execute(
                        """INSERT INTO usage(day, name, count) VALUES (?, ?, ?)
                           ON CONFLICT(day, name) DO UPDATE SET count = count + excluded.count""",
                        (key, name, float(amount)),
                    )
    except sqlite3.Error:
        pass


def cooldown_remaining(database: str, *, now: Optional[float] = None) -> float:
    current = time.time() if now is None else now
    try:
        with _db(database) as connection:
            _ensure_flags(connection)
            row = connection.execute("SELECT value FROM runtime_flags WHERE name = ?", (COOLDOWN_FLAG,)).fetchone()
    except sqlite3.Error:
        return 0.0
    try:
        return max(0.0, float(row[0]) - current) if row else 0.0
    except (TypeError, ValueError):
        return 0.0


def start_cooldown(database: str, seconds: float = COOLDOWN_SECONDS, *, reason: str = "") -> None:
    until = time.time() + seconds
    try:
        with _db(database) as connection:
            _ensure_flags(connection)
            connection.execute(
                """INSERT INTO runtime_flags(name, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET value = MAX(CAST(runtime_flags.value AS REAL), excluded.value),
                     updated_at = excluded.updated_at""",
                (COOLDOWN_FLAG, str(until), time.time()),
            )
    except sqlite3.Error:
        pass
    count_usage(database, {"llm_unavailable": 1})


def _ensure_flags(connection: sqlite3.Connection) -> None:
    connection.execute("CREATE TABLE IF NOT EXISTS runtime_flags (name TEXT PRIMARY KEY, value TEXT, updated_at REAL NOT NULL)")


# -- hashing -------------------------------------------------------------------

def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def input_hash(*, model: str, system: Optional[str], prompt: str, schema: Optional[dict],
               images: Sequence[str] = ()) -> str:
    material = {
        "model": model,
        "system": system or "",
        "prompt": prompt,
        "schema": schema,
        "images": [file_sha256(path) for path in images],
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# -- concurrency -----------------------------------------------------------------

@contextmanager
def _slot(database: str, *, wait_seconds: float) -> Iterator[int]:
    """Hold one of MICROINDIA_LLM_SLOTS slots (flock files; the OS frees them if we die)."""
    slots = max(1, int(os.environ.get("MICROINDIA_LLM_SLOTS", "2") or 2))
    directory = os.path.join(os.path.dirname(os.path.abspath(database)) or ".", "locks")
    os.makedirs(directory, exist_ok=True)
    deadline = time.time() + wait_seconds
    while True:
        for index in range(slots):
            handle = open(os.path.join(directory, f"llm-slot-{index}.lock"), "a+")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                continue
            try:
                yield index
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
            return
        if time.time() > deadline:
            raise LLMError(f"no free LLM slot after {wait_seconds:.0f}s ({slots} slots)")
        time.sleep(0.5)


# -- the CLI -------------------------------------------------------------------------

DEFAULT_SYSTEM = "You are a careful analyst. Answer exactly in the requested format."


def _argv(model: str, *, schema: Optional[dict], system: Optional[str], with_images: bool) -> List[str]:
    binary = claude_binary()
    if not binary:
        raise LLMError("claude CLI not found (set MICROINDIA_CLAUDE_BIN)")
    argv = [binary, "-p", "--model", MODELS.get(model, model)]
    if os.environ.get("ANTHROPIC_API_KEY"):
        argv.append("--bare")  # --bare authenticates only with an API key
    argv += ["--tools", "", "--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands",
             "--no-session-persistence", "--system-prompt", system or DEFAULT_SYSTEM]
    if with_images:
        argv += ["--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
    else:
        argv += ["--output-format", "json"]
    if schema is not None:
        argv += ["--json-schema", json.dumps(schema, separators=(",", ":"))]
    return argv


def _stdin(prompt: str, images: Sequence[str]) -> str:
    if not images:
        return prompt
    content: List[Dict[str, Any]] = []
    for path in images:
        media_type = mimetypes.guess_type(path)[0] or "image/jpeg"
        with open(path, "rb") as handle:
            data = base64.b64encode(handle.read()).decode()
        content.append({"type": "text", "text": f"[image: {os.path.basename(path)}]"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}})
    content.append({"type": "text", "text": prompt})
    return json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n"


def parse_envelope(stdout: str) -> Dict[str, Any]:
    """The result envelope from ``--output-format json`` (one object) or ``stream-json`` (last result line)."""
    text = stdout.strip()
    if not text:
        raise LLMError("empty output from claude")
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("type") == "result":
            return value
    raise LLMError(f"no result envelope in claude output: {text[:300]!r}")


def _model_id(envelope: Dict[str, Any], requested: str) -> str:
    usage = envelope.get("modelUsage") or {}
    wanted = MODELS.get(requested, requested)
    for name in usage:
        if wanted in name:
            return name
    if usage:  # the biggest consumer did the work (a tiny haiku call may run alongside)
        return max(usage, key=lambda name: (usage[name] or {}).get("outputTokens") or 0)
    return wanted


def _extract_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text or "", re.S)
    candidate = fenced.group(1) if fenced else (re.search(r"\{.*\}", text or "", re.S) or [None])[0]
    if candidate:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    raise LLMError(f"model reply is not JSON: {str(text)[:300]!r}")


def _required_keys(schema: Optional[dict]) -> List[str]:
    return list((schema or {}).get("required") or []) if isinstance(schema, dict) else []


def _execute(argv: List[str], *, input: str, timeout: float) -> subprocess.CompletedProcess:
    """Run the CLI in its own process group from a temp cwd, auto-updater off. On timeout the whole
    group (the CLI spawns node children) is killed and TimeoutExpired is raised."""
    env = {**os.environ, "DISABLE_AUTOUPDATER": "1"}
    with tempfile.TemporaryDirectory(prefix="microindia-llm-") as cwd:  # no CLAUDE.md above a temp dir
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, cwd=cwd, env=env, start_new_session=True)
        try:
            stdout, stderr = process.communicate(input=input, timeout=timeout)
        except BaseException:
            _kill_group(process)
            raise
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def _run_once(argv: List[str], stdin: str, timeout: float, *, schema: Optional[dict]) -> Tuple[Any, Dict[str, Any], str]:
    try:
        completed = _execute(argv, input=stdin, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise LLMError(f"claude timed out after {timeout:.0f}s") from exc
    except OSError as exc:
        raise LLMError(f"could not start claude: {exc}") from exc
    returncode, stdout, stderr = completed.returncode, completed.stdout or "", completed.stderr or ""
    try:
        envelope = parse_envelope(stdout)
    except LLMError:
        message = f"claude exited {returncode}: {(stderr or stdout)[:400]}"
        if classify_failure(stderr + stdout):
            raise LLMUnavailable(message)
        raise LLMError(message)
    if envelope.get("is_error") or returncode != 0:
        message = f"claude error ({envelope.get('subtype')}, exit {returncode}): {str(envelope.get('result'))[:400]}"
        if classify_failure(f"{envelope.get('result')} {stderr}", api_status=envelope.get("api_error_status")):
            raise LLMUnavailable(message)
        raise LLMError(message)
    if schema is not None:
        data = envelope.get("structured_output")
        if not isinstance(data, dict):
            # The CLI fell back to plain text: accept it only if it really is the schema's object.
            try:
                data = _extract_json(envelope.get("result") or "")
            except LLMError:
                data = None
        missing = [key for key in _required_keys(schema) if not isinstance(data, dict) or key not in data]
        if not isinstance(data, dict) or missing:
            raise LLMError(f"no structured output (missing {missing[:5]})")
    else:
        data = _extract_json(envelope.get("result") or "")
        if not isinstance(data, (dict, list)):
            data = {"text": envelope.get("result")}
    raw = json.dumps(envelope, ensure_ascii=False)
    return data, envelope, raw


def _kill_group(process: subprocess.Popen) -> None:
    import signal

    try:
        os.killpg(process.pid, signal.SIGKILL)  # the CLI spawns node children: take the whole group
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        process.communicate(timeout=5)
    except Exception:
        pass


def call(
    prompt: str,
    *,
    schema: Optional[dict] = None,
    model: str = "sonnet",
    images: Sequence[str] = (),
    system: Optional[str] = None,
    timeout: float = 300.0,
    cache: bool = True,
    purpose: str = "adhoc",
    database: Optional[str] = None,
    retries: int = 1,
    deadline: Optional[float] = None,
    slot_wait: Optional[float] = None,
) -> Tuple[Any, Dict[str, Any]]:
    """Run one prompt. Returns ``(parsed JSON, meta)``; meta has model, model_id, seconds, cached,
    input_hash, cost_usd, raw (the CLI envelope) and attempts.

    ``deadline`` (unix time) bounds everything: the slot wait and each attempt get only the time left,
    and no attempt starts with under a minute left, so a handler timeout never leaves a stray CLI run.
    ``slot_wait`` caps the wait for a free slot (brand chat uses a few seconds).
    Raises LLMUnavailable (Claude can't be used now; starts a shared cooldown) or LLMError."""
    database = database_path(database)
    images = [str(path) for path in images]
    key = input_hash(model=model, system=system, prompt=prompt, schema=schema, images=images)
    if cache:
        hit = _cache_get(database, key)
        if hit is not None:
            count_usage(database, {"llm_cache_hits": 1})
            return hit["output"], {"model": model, "model_id": hit["model"], "seconds": 0.0, "cached": True,
                                   "input_hash": key, "cost_usd": 0.0, "raw": hit["raw"], "attempts": 0,
                                   "purpose": purpose}
    waiting = cooldown_remaining(database)
    if waiting > 0:
        raise LLMUnavailable(f"{purpose}: Claude cooling down for {waiting:.0f}s more", retry_after=waiting)
    argv = _argv(model, schema=schema, system=system, with_images=bool(images))
    stdin = _stdin(prompt, images)
    started = time.time()
    last_error: Optional[Exception] = None
    attempts = 0
    for attempt in range(retries + 1):
        left = (deadline - time.time()) if deadline is not None else None
        if left is not None and left < MIN_ATTEMPT_SECONDS:
            last_error = last_error or LLMError(f"only {left:.0f}s left before the deadline")
            break
        attempts = attempt + 1
        try:
            wait = max(timeout * 4, 600) if slot_wait is None else slot_wait
            if left is not None:
                wait = min(wait, max(0.0, left - MIN_ATTEMPT_SECONDS))
            with _slot(database, wait_seconds=wait):
                attempt_started = time.time()
                budget = timeout if deadline is None else min(timeout, deadline - attempt_started)
                if budget < MIN_ATTEMPT_SECONDS and deadline is not None:
                    raise LLMError(f"only {budget:.0f}s left after waiting for a slot")
                data, envelope, raw = _run_once(argv, stdin, budget, schema=schema)
            seconds = time.time() - attempt_started
            cost = float(envelope.get("total_cost_usd") or 0.0)
            model_id = _model_id(envelope, model)
            count_usage(database, {"llm_calls": 1, "llm_seconds": seconds, "llm_cost_usd": cost})
            _cache_put(database, key, model_id, purpose, data, raw)
            return data, {"model": model, "model_id": model_id, "seconds": round(time.time() - started, 2),
                          "cached": False, "input_hash": key, "cost_usd": cost, "raw": raw,
                          "attempts": attempts, "purpose": purpose}
        except LLMUnavailable as exc:
            count_usage(database, {"llm_failures": 1})
            start_cooldown(database, reason=str(exc))
            raise LLMUnavailable(f"{purpose}: {exc}", retry_after=COOLDOWN_SECONDS) from exc
        except LLMError as exc:
            last_error = exc
            count_usage(database, {"llm_failures": 1})
    raise LLMError(f"{purpose}: {last_error}")
