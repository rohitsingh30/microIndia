"""The api package served for real (port 0): routing, errors, static files, body cap, SSE, and parity with
the single-file api.py it replaced (loaded from git at the B1 baseline commit and served side by side)."""

import http.client
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import test_api  # noqa: E402  (reuse its temp-DB fixture; a module import so its tests are not collected twice)

from microindia_scraper.api import make_server, router  # noqa: E402

BASELINE = "ae1f008"  # last commit with the single-file api.py
LEGACY_PATH = "apps/scraper/src/microindia_scraper/api.py"
GET_ROUTES = [
    "/api/summary", "/api/timeseries", "/api/timeseries?hours=6", "/api/activity", "/api/activity?limit=5",
    "/api/activity?after=0&limit=2", "/api/creators", "/api/creators?limit=3", "/api/creators?scope=eligible&sort=followers&order=asc",
    "/api/creators?q=pune&city=Pune", "/api/creators/peerone", "/api/creators/%40SeedCreator", "/api/creators/nobody",
    "/api/pipeline", "/api/stats", "/api/stats?hours=24", "/api/sources", "/api/nope", "/api/timeseries?hours=x",
]
VOLATILE = {"now", "server_threads", "age_seconds", "cooldown_left_s"}


def _legacy_module():
    """The pre-split api.py from git, imported inside the package so its relative imports resolve."""
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=os.path.dirname(__file__),
                             capture_output=True, text=True, check=True).stdout.strip()
        source = subprocess.run(["git", "show", f"{BASELINE}:{LEGACY_PATH}"], cwd=top,
                                capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    folder = tempfile.mkdtemp()
    path = Path(folder) / "legacy_api.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("microindia_scraper._legacy_api", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    shutil.rmtree(folder, ignore_errors=True)
    return module


def _start(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _request(server, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def _scrub(value):
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


class ServerTest(unittest.TestCase):
    def setUp(self):
        test_api.RepositoryTest.setUp(self)  # same temp DB fixture: 3 captures, 4 scrape tasks
        self.dist = Path(self.tmp.name) / "dist"
        self.dist.mkdir()
        (self.dist / "index.html").write_text("<html>app</html>")
        (self.dist / "app.js").write_text("console.log(1)")
        sibling = Path(self.tmp.name) / "dist-x"
        sibling.mkdir()
        (sibling / "secret.txt").write_text("secret")
        self.servers = []
        self.server = self._new_server(self.db)

    def tearDown(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        self.tmp.cleanup()

    def _new_server(self, database):
        server = _start(make_server(database, "127.0.0.1", 0, self.dist, str(Path(self.tmp.name) / "health.json")))
        self.servers.append(server)
        return server

    def _legacy_server(self, legacy, database):
        handler = type("LegacyHandler", (legacy.Handler,), {
            "repo": legacy.Repository(database, str(Path(self.tmp.name) / "health.json")), "dist": self.dist})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        self.servers.append(_start(server))
        return server

    def test_every_existing_get_route_answers(self):
        for path in ("/api/summary", "/api/timeseries", "/api/activity?limit=5", "/api/creators?limit=3",
                     "/api/creators/peerone", "/api/pipeline", "/api/stats?hours=24", "/api/sources"):
            status, headers, body = _request(self.server, "GET", path)
            self.assertEqual(status, 200, path)
            self.assertTrue(headers["Content-Type"].startswith("application/json"), path)
            self.assertEqual(headers["Cache-Control"], "no-store", path)
            json.loads(body)
        status, headers, body = _request(self.server, "GET", "/api/creators?format=csv")
        self.assertEqual((status, headers["Content-Type"]), (200, "text/csv; charset=utf-8"))
        self.assertIn("filename=microindia-creators.csv", headers["Content-Disposition"])
        self.assertTrue(body.startswith(b"handle,name,followers"))

    def test_unknown_routes_and_methods(self):
        self.assertEqual(_request(self.server, "GET", "/api/nope")[::2], (404, b'{"error": "unknown endpoint"}'))
        self.assertEqual(_request(self.server, "GET", "/api/creators/nobody")[::2], (404, b'{"error": "not found"}'))
        self.assertEqual(_request(self.server, "POST", "/api/nope", b"{}")[::2], (404, b'{"error": "unknown action"}'))
        self.assertEqual(_request(self.server, "POST", "/api/summary", b"{}")[0], 404)
        for method in ("PATCH", "DELETE"):
            self.assertEqual(_request(self.server, method, "/api/nope")[::2], (404, b'{"error": "unknown endpoint"}'))
        self.assertEqual(_request(self.server, "GET", "/api/timeseries?hours=x")[0], 500)

    def test_patch_and_delete_reach_their_routes(self):
        pattern = r"/api/_test_rows/(?P<row>[a-z]+)"
        router.route("PATCH", pattern)(lambda request: {"patched": request.match["row"], "body": request.body})
        router.route("DELETE", pattern)(lambda request: router.json({"deleted": request.match["row"]}, 202))

        def raise_error(request):
            raise router.HttpError(409, "busy")
        router.route("POST", pattern)(raise_error)
        try:
            status, _, body = _request(self.server, "PATCH", "/api/_test_rows/abc", b'{"note": "hi"}',
                                       {"Content-Type": "application/json"})
            self.assertEqual((status, json.loads(body)), (200, {"patched": "abc", "body": {"note": "hi"}}))
            status, _, body = _request(self.server, "DELETE", "/api/_test_rows/abc")
            self.assertEqual((status, json.loads(body)), (202, {"deleted": "abc"}))
            self.assertEqual(_request(self.server, "POST", "/api/_test_rows/abc", b"{}")[::2], (409, b'{"error": "busy"}'))
            self.assertEqual(_request(self.server, "GET", "/api/_test_rows/abc")[0], 404)
        finally:
            router._ROUTES[:] = [r for r in router._ROUTES if r[1].pattern != pattern]

    def test_static_files_and_traversal(self):
        status, headers, body = _request(self.server, "GET", "/app.js")
        self.assertEqual((status, body), (200, b"console.log(1)"))
        self.assertEqual(headers["Cache-Control"], "public, max-age=60")
        status, headers, body = _request(self.server, "GET", "/creators/peerone")  # SPA route
        self.assertEqual((status, body, headers["Cache-Control"]), (200, b"<html>app</html>", "no-cache"))
        for path in ("/../dist-x/secret.txt", "/%2e%2e/dist-x/secret.txt", "/../../etc/passwd"):
            self.assertEqual(_request(self.server, "GET", path)[2], b"<html>app</html>", path)

    def test_body_cap(self):
        status, _, body = _request(self.server, "POST", "/api/actions/seed", b"x" * (1024 * 1024 + 1))
        self.assertEqual(status, 413)
        self.assertIn(b"body over", body)
        status, _, body = _request(self.server, "POST", "/api/actions/seed", json.dumps({"usernames": "@capped"}).encode())
        self.assertEqual((status, json.loads(body)["queued"]["scrape.profile"]), (200, 1))

    def test_events_stream_opens_with_a_summary(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            connection.request("GET", "/api/events")
            response = connection.getresponse()
            self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
            # Activity from the last second may come first; the summary always follows on the first pass.
            for _ in range(40):
                if response.fp.readline() == b"event: summary\n":
                    break
            else:
                self.fail("no summary event")
            self.assertEqual(json.loads(response.fp.readline()[len(b"data: "):])["funnel"]["scraped"], 3)
            response.close()
        finally:
            connection.close()

    def test_get_routes_match_the_pre_split_api(self):
        legacy = _legacy_module()
        if legacy is None:
            if os.environ.get("MICROINDIA_ALLOW_NO_BASELINE") == "1":
                self.skipTest(f"git or {BASELINE} not available (allowed by MICROINDIA_ALLOW_NO_BASELINE)")
            self.fail(f"pre-split baseline {BASELINE} not available; set MICROINDIA_ALLOW_NO_BASELINE=1 to skip")
        old = self._legacy_server(legacy, self.db)
        for path in GET_ROUTES + ["/api/creators?format=csv", "/", "/app.js", "/some/spa/route"]:
            old_status, old_headers, old_body = _request(old, "GET", path)
            new_status, new_headers, new_body = _request(self.server, "GET", path)
            self.assertEqual(new_status, old_status, path)
            self.assertEqual(new_headers["Content-Type"], old_headers["Content-Type"], path)
            if old_headers["Content-Type"].startswith("application/json"):
                self.assertEqual(_scrub(json.loads(new_body)), _scrub(json.loads(old_body)), path)
            else:
                self.assertEqual(new_body, old_body, path)

    def test_post_actions_match_the_pre_split_api(self):
        legacy = _legacy_module()
        if legacy is None:
            if os.environ.get("MICROINDIA_ALLOW_NO_BASELINE") == "1":
                self.skipTest(f"git or {BASELINE} not available (allowed by MICROINDIA_ALLOW_NO_BASELINE)")
            self.fail(f"pre-split baseline {BASELINE} not available; set MICROINDIA_ALLOW_NO_BASELINE=1 to skip")
        old_db = str(Path(self.tmp.name) / "old.sqlite3")
        shutil.copy(self.db, old_db)
        old = self._legacy_server(legacy, old_db)
        calls = [
            ("/api/actions/seed", {"usernames": "@newone, https://www.instagram.com/newtwo/", "queries": "delhi chef"}),
            ("/api/actions/seed", {"usernames": "newone", "expand": False}),
            ("/api/actions/retry", {"reason": "Timed out"}),
            ("/api/actions/retry", {}),
            ("/api/actions/unblock", {}),
            ("/api/nope", {}),
        ]
        for path, body in calls:
            payload = json.dumps(body).encode()
            old_status, _, old_body = _request(old, "POST", path, payload)
            new_status, _, new_body = _request(self.server, "POST", path, payload)
            self.assertEqual((new_status, json.loads(new_body)), (old_status, json.loads(old_body)), path)
        self.assertEqual(json.loads(_request(self.server, "GET", "/api/pipeline")[2])["kinds"],
                         json.loads(_request(old, "GET", "/api/pipeline")[2])["kinds"])


class SharedHelpersTest(unittest.TestCase):
    def test_old_import_path_still_works_for_the_scraping_handler(self):
        from microindia_scraper import profile_text
        from microindia_scraper.api import _india, _own_words

        self.assertIs(_own_words, profile_text._own_words)
        self.assertIs(_india, profile_text._india)

    def test_city_aliases_live_in_niches(self):
        from microindia_scraper.niches import CITY_ALIASES
        from microindia_scraper.profile_text import _city

        self.assertEqual(CITY_ALIASES["bombay"], "Mumbai")
        self.assertEqual(_city({"bio_text": "Bangalore girl"}, [{"caption_text": "#mumbaifood"}]), "Bengaluru")


if __name__ == "__main__":
    unittest.main()
