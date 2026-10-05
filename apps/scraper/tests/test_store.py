import tempfile
import unittest
import time
from pathlib import Path

from microindia_scraper.constants import CONTENT_CAPTURE_LIMIT, SCHEMA_VERSION
from microindia_scraper.metrics import calculate_metrics
from microindia_scraper.models import CaptureStatus, ContentObservation, ProfileCapture, ProfileObservation
from microindia_scraper.store import CaptureStore
from microindia_scraper.intelligence import analyze_reel, derive_post_features


class CaptureStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = CaptureStore(str(Path(self.temp_dir.name) / "capture.sqlite3"))
        self.capture = ProfileCapture(
            capture_id="capture-1",
            candidate_key="instagram:creator",
            profile_url="https://www.instagram.com/creator/",
            captured_at="2026-09-26T12:00:00Z",
            schema_version=SCHEMA_VERSION,
            requested_content_count=CONTENT_CAPTURE_LIMIT,
        )
        self.store.create_capture(self.capture)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_profile_is_saved_before_content(self):
        self.store.save_profile_snapshot(
            ProfileObservation(capture_id="capture-1", handle="creator", follower_count=12500),
            "2026-09-26T12:00:03Z",
        )
        self.store.save_content_snapshot(
            ContentObservation(
                capture_id="capture-1",
                content_index=1,
                permalink="https://www.instagram.com/p/abc/",
                like_count=100,
                comment_count=10,
            ),
            "2026-09-26T12:00:05Z",
        )
        self.store.update_progress("capture-1", observed_content_count=1, last_completed_index=1)

        capture = self.store.get_capture("capture-1")
        self.assertEqual(capture["status"], CaptureStatus.RUNNING.value)
        self.assertEqual(self.store.get_saved_content_indexes("capture-1"), [1])

    def test_duplicate_content_checkpoint_is_ignored(self):
        item = ContentObservation(
            capture_id="capture-1",
            content_index=1,
            platform_content_id="post-1",
            like_count=100,
        )
        self.assertTrue(self.store.save_content_snapshot(item, "2026-09-26T12:00:05Z"))
        self.assertFalse(self.store.save_content_snapshot(item, "2026-09-26T12:00:06Z"))
        self.assertEqual(self.store.get_saved_content_indexes("capture-1"), [1])

    def test_profile_snapshot_is_immutable_within_capture(self):
        first = ProfileObservation(capture_id="capture-1", handle="creator", follower_count=100)
        second = ProfileObservation(capture_id="capture-1", handle="creator", follower_count=200)
        self.assertTrue(self.store.save_profile_snapshot(first, "2026-09-26T12:00:03Z"))
        self.assertFalse(self.store.save_profile_snapshot(second, "2026-09-26T12:00:04Z"))

    def test_metrics_use_latest_twelve_and_ignore_pinned_or_missing_values(self):
        content = [
            {
                "item_status": "observed",
                "is_pinned": index == 1,
                "content_type": "reel" if index % 2 == 0 else "post",
                "like_count": index * 10,
                "comment_count": index,
                "view_count": index * 100,
            }
            for index in range(1, 20)
        ]
        content[2]["like_count"] = None
        metrics = calculate_metrics({"follower_count": 1000}, content)
        self.assertEqual(metrics["eligible_item_count"], 12)
        self.assertEqual(metrics["metric_confidence"], "high")
        self.assertIsNotNone(metrics["engagement_rate_by_followers"])

    def test_partial_capture_retains_saved_rows(self):
        self.store.save_profile_snapshot(
            ProfileObservation(capture_id="capture-1", handle="creator"),
            "2026-09-26T12:00:03Z",
        )
        self.store.update_progress(
            "capture-1",
            observed_content_count=0,
            last_completed_index=0,
            status=CaptureStatus.NEEDS_MANUAL_AUTH,
            warnings=["AUTH_REQUIRED"],
        )
        capture = self.store.get_capture("capture-1")
        self.assertEqual(capture["status"], CaptureStatus.NEEDS_MANUAL_AUTH.value)
        self.assertEqual(capture["warnings"], ["AUTH_REQUIRED"])

    def test_job_lease_is_exclusive_and_idempotent(self):
        self.assertTrue(self.store.enqueue_job("capture_creator_profile", "profile:creator", {"profile_url": self.capture.profile_url}))
        first = self.store.lease_job("worker-a")
        second = self.store.lease_job("worker-b")
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertTrue(self.store.finish_job(first["job_id"], "worker-a"))
        self.assertIsNone(self.store.lease_job("worker-b"))

    def test_browser_page_lease_is_exclusive_and_releasable(self):
        self.store.register_browser_page("target-a", "owner-a")
        first = self.store.lease_browser_page("worker-a")
        second = self.store.lease_browser_page("worker-b")
        self.assertEqual(first["target_id"], "target-a")
        self.assertIsNone(second)
        self.assertTrue(self.store.release_browser_page("target-a", "worker-a"))
        self.assertEqual(self.store.lease_browser_page("worker-b")["target_id"], "target-a")

    def test_expired_browser_page_lease_is_recoverable(self):
        self.store.register_browser_page("target-a", "owner-a")
        self.store.connection.execute("UPDATE browser_pages SET lease_until = ?", (time.time() - 1,))
        self.store.connection.commit()
        page = self.store.lease_browser_page("worker-b")
        self.assertEqual(page["target_id"], "target-a")

    def test_collection_attempt_is_recorded(self):
        self.store.enqueue_job("capture_creator_profile", "profile:attempt", {"profile_url": self.capture.profile_url})
        job = self.store.lease_job("worker-a")
        attempt_id = self.store.start_attempt(job["job_id"], "worker-a", "100.0")
        self.store.finish_attempt(attempt_id, outcome="failed", error_class="TimeoutError", error_message="timed out", finished_at="101.0")
        row = self.store.connection.execute("SELECT outcome, error_class, finished_at FROM collection_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        self.assertEqual(dict(row), {"outcome": "failed", "error_class": "TimeoutError", "finished_at": "101.0"})

    def test_post_feature_derivation_keeps_missing_metrics_null(self):
        features = derive_post_features({"caption_text": "New #food recipe #ad", "like_count": 10, "comment_count": None}, 1000)
        self.assertEqual(features["hashtag_count"], 2)
        self.assertTrue(features["commercial_disclosure"])
        self.assertIsNone(features["engagement_rate"])

    def test_reel_analysis_profiles_topic_hook_language_and_cta(self):
        analysis = analyze_reel({
            "content_type": "reel",
            "caption_text": "How to make this Delhi recipe — save and follow for more! आज की आसान रेसिपी #food",
            "text_content": "page body with comments",
        })
        self.assertEqual(analysis["topic"], "food")
        self.assertTrue(analysis["call_to_action"])
        self.assertTrue(analysis["has_extracted_text"])
        self.assertIn("hi", analysis["language_signals"])

    def test_reel_features_are_persisted_for_each_reel(self):
        features = derive_post_features({"content_type": "reel", "caption_text": "Travel tips in Kerala. Save this guide"}, 10000)
        self.assertEqual(features["reel_analysis"]["topic"], "travel")
        self.assertTrue(features["reel_analysis"]["call_to_action"])

    def test_capture_features_materialize_creator_and_post(self):
        self.store.save_profile_snapshot(ProfileObservation(capture_id="capture-1", handle="creator", follower_count=1000), "2026-09-26T12:00:03Z")
        self.store.save_content_snapshot(ContentObservation(capture_id="capture-1", content_index=1, platform_content_id="post-1", permalink="https://www.instagram.com/p/post-1/", caption_text="food recipe #food", like_count=100, comment_count=10), "2026-09-26T12:00:05Z")
        self.store.save_metrics("capture-1", {"engagement_rate_by_followers": 0.11, "metric_confidence": "low"}, "2026-09-26T12:00:06Z")
        creator_id = self.store.materialize_capture_features("capture-1", "features-v1", "2026-09-26T12:00:07Z")
        self.assertIsNotNone(creator_id)
        creators = self.store.discover_creators(category="food", min_followers=500)
        self.assertEqual(len(creators), 1)
        self.assertEqual(creators[0]["features"]["primary_category"], "food")

    def test_quarantined_capture_retains_ai_observations_and_features(self):
        capture = ProfileCapture(
            capture_id="capture-ai",
            candidate_key="instagram:ai_creator",
            profile_url="https://www.instagram.com/ai_creator/",
            captured_at="2026-09-26T12:00:00Z",
            schema_version=SCHEMA_VERSION,
        )
        self.store.create_capture(capture)
        self.store.save_profile_snapshot(
            ProfileObservation(
                capture_id="capture-ai",
                handle="ai_creator",
                display_name="AI Creator",
                bio_text="Food creator in Delhi, India",
                follower_count=20_000,
                location_text="Delhi, India",
                ai_label="ai_dominant",
                ai_signals=["profile_ai_creator"],
                ai_evidence=["AI creator"],
            ),
            "2026-09-26T12:00:03Z",
        )
        for index in range(1, 9):
            self.store.save_content_snapshot(
                ContentObservation(
                    capture_id="capture-ai",
                    content_index=index,
                    platform_content_id=f"ai-post-{index}",
                    permalink=f"https://www.instagram.com/p/ai-post-{index}/",
                    caption_text="generated with AI",
                    ai_label="ai_generated",
                    ai_score=0.95,
                    ai_signals=["ai_generated"],
                    ai_evidence=["generated with AI"],
                ),
                f"2026-09-26T12:00:{index + 3:02d}Z",
            )
        self.store.save_metrics("capture-ai", {"metric_confidence": "low"}, "2026-09-26T12:01:00Z")
        self.store.materialize_capture_features("capture-ai", "features-v1", "2026-09-26T12:01:01Z")
        self.store.update_progress(
            "capture-ai",
            observed_content_count=8,
            last_completed_index=8,
            status=CaptureStatus.QUARANTINED,
            warnings=["QUALITY_GATE:AI_PROFILE_DOMINANT"],
            completeness_score=80.0,
        )
        capture_row = self.store.get_capture("capture-ai")
        self.assertEqual(capture_row["status"], "quarantined")
        self.assertEqual(len(self.store.get_content_payloads("capture-ai")), 8)
        features = self.store.discover_creators(query="ai_creator")[0]["features"]
        self.assertEqual(features["ai_analysis"]["ai_label"], "ai_dominant")

    def test_candidate_frontier_retains_provenance_and_leases(self):
        record = {
            "platform": "instagram",
            "username": "frontier_creator",
            "profile_url": "https://www.instagram.com/frontier_creator/",
            "is_private": False,
            "source_name": "approved-index",
            "source_record_key": "frontier-1",
            "observed_at": "2026-09-27T00:00:00Z",
        }
        self.assertTrue(self.store.upsert_candidate(record))
        self.assertTrue(self.store.upsert_candidate(record))
        self.assertEqual(
            self.store.connection.execute("SELECT COUNT(*) FROM candidate_discoveries").fetchone()[0],
            1,
        )
        self.assertIsNotNone(self.store.lease_candidate("instagram:frontier_creator", "worker-a"))

    def test_candidate_cursor_and_expired_lease_recovery(self):
        self.store.save_source_cursor("approved-index", "128")
        self.assertEqual(self.store.load_source_cursor("approved-index"), "128")
        record = {
            "platform": "instagram",
            "username": "lease_creator",
            "profile_url": "https://www.instagram.com/lease_creator/",
            "is_private": False,
            "source_name": "approved-index",
            "source_record_key": "lease-1",
            "observed_at": "2026-09-27T00:00:00Z",
        }
        self.assertTrue(self.store.enqueue_candidate_job(record))
        first = self.store.lease_verified_candidate("worker-a", lease_seconds=-1)
        self.assertIsNotNone(first)
        second = self.store.lease_verified_candidate("worker-b")
        self.assertIsNotNone(second)
        self.assertEqual(second["leased_by"], "worker-b")

if __name__ == "__main__":
    unittest.main()