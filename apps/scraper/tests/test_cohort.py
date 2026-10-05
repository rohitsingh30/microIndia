import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from microindia_scraper.cohort import (
    cohort_status,
    deterministic_slot_queries,
    discover_profile_urls,
    eligible_profile_count,
    enqueue_candidates,
    enqueue_stale_refreshes,
)
from microindia_scraper.models import CaptureStatus, ProfileCapture, ProfileObservation
from microindia_scraper.store import CaptureStore


class FakeDiscoveryPage:
    def __init__(self, result, links=None):
        self.result = result
        self.links = links
        self.scrolls = 0

    async def evaluate(self, script):
        if "fetch(" in script:
            return self.result
        if "scrollBy" in script:
            self.scrolls += 1
            return True
        return self.links


class CohortTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = CaptureStore(str(Path(self.temp_dir.name) / "cohort.sqlite3"))

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_enqueue_candidates_deduplicates_urls(self):
        urls = ["https://www.instagram.com/creator/", "https://www.instagram.com/creator/"]
        self.assertEqual(enqueue_candidates(self.store, urls, metadata={"is_private": False, "source_name": "test-source", "source_record_key": "creator", "observed_at": "2026-09-27T00:00:00Z"}), 1)
        self.assertEqual(enqueue_candidates(self.store, urls), 0)

    def test_counts_only_eligible_profiles(self):
        capture = ProfileCapture("capture-1", "instagram:creator", "https://www.instagram.com/creator/", "2026-09-26T00:00:00Z", "v1", status=CaptureStatus.COMPLETE)
        self.store.create_capture(capture)
        self.store.save_profile_snapshot(ProfileObservation(capture_id="capture-1", handle="creator", display_name="Asha", bio_text="Delhi food creator", follower_count=20_000, is_private=False, location_text="Delhi India"), "2026-09-26T00:00:01Z")
        self.assertEqual(eligible_profile_count(self.store), 1)
        self.assertEqual(cohort_status(self.store, 1000)["remaining"], 999)


    def test_stale_refresh_is_bounded_and_bucket_idempotent(self):
        now = 1_800_000_000.0
        captured_at = datetime.fromtimestamp(now - 7_200, timezone.utc).isoformat()
        capture = ProfileCapture(
            "capture-refresh",
            "instagram:creator",
            "https://www.instagram.com/creator/",
            captured_at,
            "v1",
            status=CaptureStatus.COMPLETE,
        )
        self.store.create_capture(capture)
        self.store.save_profile_snapshot(
            ProfileObservation(
                capture_id="capture-refresh",
                handle="creator",
                bio_text="Delhi food creator",
                follower_count=20_000,
                is_private=False,
                location_text="Delhi India",
            ),
            captured_at,
        )

        self.assertEqual(
            enqueue_stale_refreshes(
                self.store,
                stale_after_seconds=3_600,
                limit=1,
                now=now,
            ),
            1,
        )
        self.assertEqual(
            enqueue_stale_refreshes(
                self.store,
                stale_after_seconds=3_600,
                limit=1,
                now=now,
            ),
            0,
        )
        job = self.store.connection.execute(
            "SELECT payload FROM collection_jobs WHERE status = 'queued'"
        ).fetchone()
        self.assertIn('"reason": "stale_refresh"', job["payload"])


    def test_discovery_never_falls_back_to_unverified_feed_links(self):
        page = FakeDiscoveryPage(
            {"status": 200, "final_url": "https://www.instagram.com/", "users": []},
            [
                {
                    "url": "https://www.instagram.com/Creator/?utm_source=feed",
                    "text": "Food creator 20K followers",
                },
                {
                    "url": "https://www.instagram.com/private_creator/",
                    "text": "Private account food creator 20K followers",
                },
            ],
        )
        self.assertEqual(asyncio.run(discover_profile_urls(page, "food creator")), [])
        self.assertEqual(page.scrolls, 0)

    def test_search_filters_private_out_of_range_and_wrong_category_users(self):
        page = FakeDiscoveryPage(
            {
                "status": 200,
                "final_url": "https://www.instagram.com/",
                "users": [
                    {
                        "username": "public_food",
                        "is_private": False,
                        "follower_count": 20_000,
                        "biography": "Mumbai food creator",
                    },
                    {
                        "username": "private_food",
                        "is_private": True,
                        "follower_count": 20_000,
                        "biography": "Mumbai food creator",
                    },
                    {
                        "username": "large_food",
                        "is_private": False,
                        "follower_count": 120_000,
                        "biography": "Mumbai food creator",
                    },
                    {
                        "username": "travel_creator",
                        "is_private": False,
                        "follower_count": 20_000,
                        "biography": "Mumbai travel creator",
                    },
                    {
                        "username": "public_unknown_range",
                        "is_private": False,
                        "biography": "Mumbai food creator",
                    },
                    {
                        "username": "unknown_privacy_food",
                        "follower_count": 20_000,
                        "biography": "Mumbai food creator",
                    },
                ],
            }
        )
        self.assertEqual(
            asyncio.run(discover_profile_urls(page, "food creator")),
            [
                "https://www.instagram.com/public_food/",
                "https://www.instagram.com/public_unknown_range/",
            ],
        )


    def test_discovery_html_login_marker_requires_manual_auth(self):
        page = FakeDiscoveryPage({
            "status": 302,
            "final_url": "https://www.instagram.com/accounts/login/",
            "users": [],
            "html": "<html>Log in to Instagram</html>",
        })
        with self.assertRaisesRegex(RuntimeError, "manual authentication"):
            asyncio.run(discover_profile_urls(page, "food creator"))

    def test_discovery_slots_are_reproducible_and_ten_wide(self):
        first = deterministic_slot_queries(1234, 2)
        second = deterministic_slot_queries(1234, 2)
        self.assertEqual(first, second)
        self.assertEqual([item["agent_slot"] for item in first], list(range(10)))
        self.assertTrue(all(item["selection_seed"] == 1234 for item in first))
if __name__ == "__main__":
    unittest.main()