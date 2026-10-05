import json
import tempfile
import unittest
from pathlib import Path

from microindia_scraper.backfill_reel_analysis import backfill
from microindia_scraper.models import ContentObservation, ProfileCapture, ProfileObservation
from microindia_scraper.store import CaptureStore


class ReelBackfillTests(unittest.TestCase):
    def test_backfill_reads_raw_observation_and_is_resumable(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "backfill.sqlite3")
            store = CaptureStore(database)
            store.create_capture(ProfileCapture(
                capture_id="capture-1",
                candidate_key="instagram:creator",
                profile_url="https://www.instagram.com/creator/",
                captured_at="2026-09-26T12:00:00Z",
                schema_version="profile-capture-v1",
            ))
            store.save_profile_snapshot(ProfileObservation(capture_id="capture-1", handle="creator", follower_count=10000), "2026-09-26T12:00:01Z")
            store.save_content_snapshot(ContentObservation(
                capture_id="capture-1",
                content_index=1,
                platform_content_id="reel-1",
                permalink="https://www.instagram.com/reel/reel-1/",
                content_type="reel",
                caption_text="How to make a travel guide. Save this!",
                like_count=100,
                comment_count=10,
            ), "2026-09-26T12:00:02Z")
            store.materialize_capture_features("capture-1", "features-v1", "2026-09-26T12:00:03Z")
            store.connection.execute(
                "UPDATE post_features SET payload = ?, feature_version = ?",
                (json.dumps({"content_type": "reel", "like_count": 100, "comment_count": 10}), "features-v0"),
            )
            store.connection.commit()
            store.close()

            first = backfill(database, batch_size=1)
            second = backfill(database, batch_size=1)
            self.assertEqual(first["updated"], 1)
            self.assertEqual(second["updated"], 0)
            self.assertEqual(second["skipped"], 1)

            connection = __import__("sqlite3").connect(database)
            payload = json.loads(connection.execute("SELECT payload FROM post_features").fetchone()[0])
            connection.close()
            self.assertEqual(payload["reel_analysis"]["topic"], "travel")


if __name__ == "__main__":
    unittest.main()