import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from microindia_scraper.candidate_source import validate_candidate_record
from microindia_scraper.e2e import run as run_capture
from microindia_scraper.shared_dispatcher import (
    dispatch_once,
    ingest_for_you_batch,
    preflight_public_candidate,
    source_ingestion_allowed,
    stable_shard_assignment,
)
from microindia_scraper.store import CaptureStore
from microindia_scraper.cohort import discover_for_you_profile_urls


class FakePage:
    def __init__(self, response):
        self.response = response
        self.gotos = []
        self.evaluations = []

    async def goto(self, url):
        self.gotos.append(url)

    async def evaluate(self, script):
        self.evaluations.append(script)
        if "fetch(" in script:
            return self.response
        return {
            "url": "https://www.instagram.com/demo/",
            "title": "demo",
            "description": "A public creator",
            "bodyText": "Public creator",
            "headerText": "demo",
            "bioText": "Food creator",
            "structuredProfile": {},
            "isPrivate": False,
            "isVerified": False,
            "externalUrl": None,
            "links": [],
        }

class ForYouPage(FakePage):
    def __init__(self, authors, responses):
        super().__init__(None)
        self.url = "https://www.instagram.com/"
        self.authors = authors
        self.responses = responses

    async def evaluate(self, script):
        if "fetch(" in script:
            profile_url = script.split('fetch("', 1)[1].split('"', 1)[0]
            return self.responses[profile_url]
        return self.authors

class FakeBrowser:
    def __init__(self, pages):
        self.pages = pages

    async def get_pages(self):
        return self.pages


class PublicProfileScaleTests(unittest.TestCase):
    def candidate(self, username="demo", key="1"):
        return {
            "platform": "instagram",
            "username": username,
            "profile_url": f"https://www.instagram.com/{username}/",
            "is_private": False,
            "source_name": "approved-index",
            "source_record_key": key,
            "observed_at": "2026-09-27T00:00:00Z",
        }

    def test_source_record_requires_literal_public_evidence(self):
        with self.assertRaises(ValueError):
            validate_candidate_record({**self.candidate(), "is_private": True})
        with self.assertRaises(ValueError):
            validate_candidate_record({key: value for key, value in self.candidate().items() if key != "is_private"})
        with self.assertRaises(ValueError):
            validate_candidate_record({**self.candidate(), "profile_url": "https://www.instagram.com/explore/"})
        self.assertEqual(validate_candidate_record(self.candidate()).candidate_key, "instagram:demo")

    def test_for_you_reels_author_links_normalize_to_profiles(self):
        page = ForYouPage(
            ["https://www.instagram.com/team_kauravas/reels/", "https://www.instagram.com/reels/"],
            {},
        )
        self.assertEqual(
            asyncio.run(discover_for_you_profile_urls(page, limit=10)),
            ["https://www.instagram.com/team_kauravas/"],
        )

    def test_frontier_idempotency_provenance_and_conditional_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CaptureStore(str(Path(directory) / "frontier.sqlite3"))
            record = self.candidate()
            self.assertTrue(store.upsert_candidate(record))
            self.assertTrue(store.upsert_candidate(record))
            self.assertTrue(store.enqueue_candidate_job(record))
            self.assertFalse(store.enqueue_candidate_job(record))
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM candidate_discoveries").fetchone()[0], 1)
            self.assertFalse(store.mark_candidate("instagram:demo", "verified_public", "captured"))
            leased = store.lease_candidate("instagram:demo", "worker-a")
            self.assertIsNotNone(leased)
            self.assertFalse(store.mark_candidate("instagram:demo", "queued", "captured"))
            self.assertTrue(store.mark_candidate_owned("instagram:demo", "worker-a", "leased", "captured"))
            store.close()

    def test_cursor_restart_and_rejections_are_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.ndjson"
            source.write_text(json.dumps(self.candidate()) + "\n" + "not-json\n")
            store = CaptureStore(str(Path(directory) / "frontier.sqlite3"))
            from microindia_scraper.shared_dispatcher import ingest_source_batch
            first = ingest_source_batch(store, str(source))
            second = ingest_source_batch(store, str(source))
            self.assertEqual(first["jobs_added"], 1)
            self.assertEqual(first["rejected"], 1)
            self.assertEqual(second["jobs_added"], 0)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM source_rejections").fetchone()[0], 1)
            self.assertIsNotNone(store.load_source_cursor(str(source)))
            store.close()

    def test_for_you_authors_are_preflighted_before_enqueue(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CaptureStore(str(Path(directory) / "for-you.sqlite3"))
            public_url = "https://www.instagram.com/public_creator/"
            private_url = "https://www.instagram.com/private_creator/"
            page = ForYouPage(
                [public_url, private_url, "https://www.instagram.com/reels/"],
                {
                    public_url: {"status": 200, "data": {"is_private": False}},
                    private_url: {"status": 200, "data": {"is_private": True}},
                },
            )
            result = asyncio.run(ingest_for_you_batch(page, store, limit=10))
            self.assertEqual(page.gotos, ["https://www.instagram.com/reels/"])
            self.assertEqual(result["public"], 1)
            self.assertEqual(result["rejected"], 1)
            self.assertEqual(result["jobs_added"], 1)
            self.assertEqual(
                store.connection.execute("SELECT COUNT(*) FROM collection_jobs").fetchone()[0],
                1,
            )
            self.assertEqual(
                store.connection.execute("SELECT source_name FROM profile_candidates").fetchone()[0],
                "instagram-for-you",
            )
            store.close()

    def test_preflight_has_no_navigation_and_requires_explicit_public(self):
        public_page = FakePage({"status": 200, "data": {"is_private": False}})
        result = asyncio.run(preflight_public_candidate(public_page, {"profile_url": "https://www.instagram.com/demo/"}))
        self.assertEqual(result["status"], "public")
        self.assertEqual(public_page.gotos, [])
        unknown_page = FakePage({"status": 200, "body": "profile response"})
        result = asyncio.run(preflight_public_candidate(unknown_page, {"profile_url": "https://www.instagram.com/demo/"}))
        self.assertEqual(result["status"], "unknown")
        fyp_page = FakePage({"status": 200, "body": "profile response"})
        result = asyncio.run(preflight_public_candidate(fyp_page, {
            "profile_url": "https://www.instagram.com/demo/",
            "discovery_surface": "instagram_reels_for_you",
            "public_evidence": "FYP_VISIBLE_REEL_AUTHOR",
        }))
        self.assertEqual(result["status"], "public")
        private_page = FakePage({"status": 200, "data": {"is_private": True}})
        result = asyncio.run(preflight_public_candidate(private_page, {"profile_url": "https://www.instagram.com/demo/"}))
        self.assertEqual(result["status"], "private")
    def test_dispatch_private_preflight_quarantines_without_capture_navigation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CaptureStore(str(Path(directory) / "dispatch.sqlite3"))
            self.assertTrue(store.enqueue_candidate_job(self.candidate()))
            discovery = FakePage({"status": 200, "data": {"is_private": True}})
            capture_page = FakePage({"status": 200})
            discovery.target_id = "discovery"
            capture_page.target_id = "capture"
            store.register_browser_page("discovery", "owner")
            store.register_browser_page("capture", "owner")
            browser = FakeBrowser([discovery, capture_page])
            result = asyncio.run(dispatch_once(
                browser, store, "owner", str(Path(directory) / "dispatch.sqlite3"),
                discovery_target_id="discovery",
            ))
            self.assertTrue(result)
            self.assertEqual(capture_page.gotos, [])
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM profile_captures").fetchone()[0], 0)
            self.assertEqual(
                store.connection.execute("SELECT status FROM profile_candidates WHERE candidate_key='instagram:demo'").fetchone()[0],
                "quarantined",
            )
            self.assertEqual(store.connection.execute("SELECT status FROM collection_jobs").fetchone()[0], "failed")
            store.close()

    def test_dispatch_public_preflight_is_required_before_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CaptureStore(str(Path(directory) / "dispatch.sqlite3"))
            self.assertTrue(store.enqueue_candidate_job(self.candidate()))
            discovery = FakePage({"status": 200, "data": {"is_private": False}})
            capture_page = FakePage({"status": 200})
            discovery.target_id = "discovery"
            capture_page.target_id = "capture"
            store.register_browser_page("discovery", "owner")
            store.register_browser_page("capture", "owner")
            browser = FakeBrowser([discovery, capture_page])

            async def fake_capture(*args, **kwargs):
                return {"status": "partial", "capture_id": "capture-1"}

            with patch("microindia_scraper.shared_dispatcher.run_capture", side_effect=fake_capture) as capture:
                self.assertTrue(asyncio.run(dispatch_once(
                    browser, store, "owner", str(Path(directory) / "dispatch.sqlite3"),
                    discovery_target_id="discovery",
                )))
            capture.assert_called_once()
            self.assertEqual(capture_page.gotos, [])
            self.assertEqual(
                store.connection.execute("SELECT status FROM profile_candidates WHERE candidate_key='instagram:demo'").fetchone()[0],
                "captured",
            )
            store.close()


    def test_backpressure_and_sharding_are_deterministic(self):
        self.assertEqual(source_ingestion_allowed(1000, 1000, 250, False), (False, True))
        self.assertEqual(source_ingestion_allowed(500, 1000, 250, True), (False, True))
        self.assertEqual(source_ingestion_allowed(250, 1000, 250, True), (True, False))
        assignments = [stable_shard_assignment(f"instagram:{name}", 3) for name in ("a", "b", "c")]
        self.assertEqual(assignments, [stable_shard_assignment(f"instagram:{name}", 3) for name in ("a", "b", "c")])

    def test_missing_profile_url_fails_before_browser_start(self):
        with self.assertRaisesRegex(ValueError, "capture requires an explicit profile_url"):
            asyncio.run(run_capture(None, None, "Default", ":memory:"))

    def test_private_profile_recheck_stops_before_content_links(self):
        with tempfile.TemporaryDirectory() as directory:
            page = FakePage({"status": 200})
            page.evaluate = self._private_evaluate(page)
            result = asyncio.run(run_capture(
                "https://www.instagram.com/demo/",
                None,
                "Default",
                str(Path(directory) / "capture.sqlite3"),
                browser_session=object(),
                page=page,
            ))
            self.assertEqual(result["status"], "quarantined")
            self.assertEqual(page.gotos, ["https://www.instagram.com/demo/"])
            self.assertFalse(any("filter(href" in script for script in page.evaluations))

    @staticmethod
    def _private_evaluate(page):
        async def evaluate(script):
            page.evaluations.append(script)
            return {
                "url": "https://www.instagram.com/demo/",
                "title": "demo",
                "description": "Private account",
                "bodyText": "Private account",
                "headerText": "demo",
                "bioText": None,
                "structuredProfile": {},
                "isPrivate": True,
                "isVerified": False,
                "externalUrl": None,
                "links": [],
            }
        return evaluate


if __name__ == "__main__":
    unittest.main()
