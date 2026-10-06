"""Push microIndia data or code to the dashboard running on Nikamma.

    python3 deploy/push.py snapshot [--force]   # new database snapshot, only if the data changed
    python3 deploy/push.py code [--bundle FILE]  # code bundle (src + built dashboard); CI uses this too
    python3 deploy/push.py status

The deploy token is read from MICROINDIA_DEPLOY_TOKEN, else from the macOS Keychain item
"microindia-nikamma-deploy". The server only knows its SHA-256 (see apps/microindia in Nikamma).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_URL = os.environ.get("MICROINDIA_DEPLOY_URL", "https://microindia.nikamma.in")
DEFAULT_SOURCE = os.environ.get("MICROINDIA_SOURCE_DB",
                                "/Users/rohit/projects/microIndia/apps/scraper/data/microindia.sqlite3")
STATE_FILE = Path.home() / ".microindia-deploy-state.json"
KEYCHAIN_SERVICE = "microindia-nikamma-deploy"


def log(**event) -> None:
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **event}, sort_keys=True), flush=True)


def token() -> str:
    value = os.environ.get("MICROINDIA_DEPLOY_TOKEN")
    if value:
        return value.strip()
    found = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                           capture_output=True, text=True)
    if found.returncode != 0 or not found.stdout.strip():
        raise SystemExit(f"no deploy token: set MICROINDIA_DEPLOY_TOKEN or add Keychain item {KEYCHAIN_SERVICE!r}")
    return found.stdout.strip()


def request(url: str, method: str = "GET", body: bytes = b"", content_type: str = "application/octet-stream") -> dict:
    req = urllib.request.Request(url, data=body if method == "POST" else None, method=method, headers={
        "Authorization": f"Bearer {token()}", "Content-Type": content_type, "User-Agent": "microindia-deploy/1"})
    try:
        with urllib.request.urlopen(req, timeout=600) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{method} {url} -> {exc.code}: {exc.read()[:300].decode(errors='replace')}")


def _fingerprint(path: str) -> list:
    """Changes whenever the data does (row counts and newest timestamps), not when a reader touches the file."""
    import sqlite3

    queries = (
        "SELECT COUNT(*), MAX(updated_at) FROM tasks",
        "SELECT COUNT(*), MAX(captured_at) FROM profile_captures",
        "SELECT COUNT(*), MAX(created_at) FROM reel_analyses",
        "SELECT COUNT(*), MAX(created_at) FROM creator_dossiers",
    )
    connection = sqlite3.connect(path, timeout=30)
    try:
        values = []
        for query in queries:
            try:
                values.append(list(connection.execute(query).fetchone()))
            except sqlite3.OperationalError:
                values.append(None)
        return values
    finally:
        connection.close()


def push_snapshot(url: str, source: str, force: bool) -> None:
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    fingerprint = _fingerprint(source)
    if not force and fingerprint and fingerprint == state.get("snapshot_fingerprint"):
        log(event="snapshot_unchanged", source=source)
        return
    sys.path.insert(0, str(HERE))
    from make_snapshot import build  # noqa: E402

    packed = build(source, str(HERE / "out"))
    result = request(f"{url}/__deploy/snapshot", "POST", Path(packed).read_bytes())
    state.update(snapshot_fingerprint=fingerprint, snapshot_pushed_at=time.time(), snapshot=result.get("snapshot"))
    STATE_FILE.write_text(json.dumps(state))
    log(event="snapshot_pushed", **result)


def bundle(root: Path) -> bytes:
    src, dist = root / "apps/scraper/src", root / "apps/dashboard/dist"
    if not (dist / "index.html").exists():
        raise SystemExit(f"{dist}/index.html missing: build the dashboard first (npm run build)")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        archive.add(src, arcname="src", filter=lambda info: None if "__pycache__" in info.name else info)
        archive.add(dist, arcname="dist")
    return buffer.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("snapshot", "code", "status"))
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--bundle", help="tar.gz to push instead of bundling this checkout")
    args = parser.parse_args()
    url = args.url.rstrip("/")
    if args.command == "snapshot":
        push_snapshot(url, args.source, args.force)
    elif args.command == "code":
        body = Path(args.bundle).read_bytes() if args.bundle else bundle(ROOT)
        log(event="code_pushed", **request(f"{url}/__deploy/code", "POST", body, "application/gzip"))
    else:
        print(json.dumps(request(f"{url}/__deploy/status"), indent=2))


if __name__ == "__main__":
    main()
