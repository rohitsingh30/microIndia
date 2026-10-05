import json
import tempfile
import time
import unittest
from pathlib import Path

from microindia_scraper.api import Repository, _reason_bucket
from microindia_scraper.constants import CONTENT_CAPTURE_LIMIT, SCHEMA_VERSION
from microindia_scraper.models import CaptureStatus, ContentObservation, ProfileCapture, ProfileObservation
from microindia_scraper.runtime import Done, Retry, Skip, TaskStore
from microindia_scraper.store import CaptureStore


def add_capture(store, handle, followers, status, captured_at="2026-10-05T10:00:00+00:00"):
    capture_id = f"cap-{handle}-{captured_at}"
    store.create_capture(ProfileCapture(
        capture_id=capture_id, candidate_key=f"instagram:{handle}",
        profile_url=f"https://www.instagram.com/{handle}/", captured_at=captured_at,
        schema_version=SCHEMA_VERSION, requested_content_count=CONTENT_CAPTURE_LIMIT,
    ))
    store.save_profile_snapshot(ProfileObservation(
        capture_id=capture_id, handle=handle, display_name=handle.title(), follower_count=followers,
        bio_text=f"{handle} food from Pune", location_text="Pune", language_signals=["en", "hi"],
    ), captured_at)
    store.save_content_snapshot(ContentObservation(
        capture_id=capture_id, content_index=1, permalink=f"https://www.instagram.com/p/{handle}1/",
        like_count=500, comment_count=20, mentions=["@friend"],
    ), captured_at)
    store.update_progress(capture_id, observed_content_count=1, last_completed_index=1, status=status)
    return capture_id


class RepositoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "t.sqlite3")
        store = CaptureStore(self.db)
        add_capture(store, "seedcreator", 40000, CaptureStatus.COMPLETE)
        add_capture(store, "peerone", 25000, CaptureStatus.COMPLETE)
        add_capture(store, "bigbrand", 900000, CaptureStatus.QUARANTINED)
        store.close()
        tasks = TaskStore(self.db)
        tasks.enqueue("scrape.profile", "seedcreator", {"username": "seedcreator"})
        tasks.enqueue("scrape.profile", "peerone", {"username": "peerone", "found_via": "similar:seedcreator"})
        tasks.enqueue("scrape.profile", "bigbrand", {"username": "bigbrand", "found_via": "search:pune food"})
        tasks.enqueue("scrape.profile", "flaky", {"username": "flaky", "found_via": "mention:peerone"})
        for _ in range(4):
            task = tasks.lease("r", ["scrape.profile"])
            result = {
                "seedcreator": Done({"eligible": True, "followers": 40000}),
                "peerone": Done({"eligible": True, "followers": 25000}),
                "bigbrand": Skip("ELIGIBILITY:outside 10K-100K micro-influencer band", {"eligible": False}),
                "flaky": Retry("TimeoutError: page load"),
            }[task["key"]]
            tasks.apply(task, "r", result)
        tasks.close()
        self.repo = Repository(self.db, health_file=str(Path(self.tmp.name) / "health.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_creators_list_everything_scraped_with_fit_filters_and_sort(self):
        everything = self.repo.list_creators({})
        self.assertEqual({c["handle"] for c in everything["items"]}, {"seedcreator", "peerone", "bigbrand"})
        self.assertEqual({c["handle"] for c in self.repo.list_creators({"scope": "eligible"})["items"]}, {"seedcreator", "peerone"})
        ranked = self.repo.list_creators({"sort": "followers", "order": "asc", "scope": "eligible"})
        self.assertEqual([c["handle"] for c in ranked["items"]], ["peerone", "seedcreator"])
        self.assertEqual(self.repo.list_creators({"min_followers": "30000", "max_followers": "100000"})["total"], 1)
        self.assertEqual(self.repo.list_creators({"q": "pune", "scope": "eligible"})["total"], 2)
        self.assertEqual(self.repo.list_creators({"source": "similar"})["items"][0]["handle"], "peerone")
        best = self.repo.list_creators({"sort": "fit"})["items"][0]
        self.assertNotEqual(best["handle"], "bigbrand")  # 900K followers is out of the default fit range
        self.assertEqual(self.repo.list_creators({"city": "Pune"})["total"], 3)

    def test_search_matches_hashtags_stored_as_objects(self):
        import sqlite3
        connection = sqlite3.connect(self.db)
        connection.execute("""INSERT OR IGNORE INTO creators(platform, canonical_key, handle, profile_url, created_at, updated_at)
            VALUES ('instagram', 'instagram:peerone', 'peerone', 'https://www.instagram.com/peerone/', 'now', 'now')""")
        connection.execute("""INSERT OR REPLACE INTO creator_features(creator_id, feature_version, payload, calculated_at)
            SELECT creator_id, 'v', json('{"top_hashtags": [{"tag": "#punefoodie", "count": 3}], "follower_count": 25000}'), 'now'
            FROM creators WHERE handle='peerone'""")
        connection.commit(); connection.close()
        self.repo._creators_version = None
        self.repo._built_at = 0.0
        result = self.repo.list_creators({"q": "punefoodie"})
        self.assertEqual([c["handle"] for c in result["items"]], ["peerone"])
        self.assertEqual(result["items"][0]["top_hashtags"], ["#punefoodie"])

    def test_csv_export(self):
        csv_text = self.repo.list_creators({"format": "csv"})["csv"]
        self.assertTrue(csv_text.startswith("handle,name,followers"))
        self.assertIn("peerone", csv_text)

    def test_detail_has_posts_history_and_provenance(self):
        detail = self.repo.creator_detail("@PeerOne")
        self.assertEqual(detail["followers"], 25000)
        self.assertEqual(len(detail["posts_sample"]), 1)
        self.assertEqual(detail["provenance"], [{"via": "similar", "from": "seedcreator"}, {"via": "seed", "from": ""}])
        self.assertIsNone(self.repo.creator_detail("nobody"))

    def test_summary_funnel_and_pipeline(self):
        summary = self.repo.summary()
        self.assertEqual(summary["funnel"]["scraped"], 3)
        self.assertEqual((summary["funnel"]["kept"], summary["funnel"]["dropped"]), (2, 1))
        self.assertEqual(summary["queue"], 1)
        pipeline = self.repo.pipeline()
        self.assertEqual(pipeline["kinds"]["scrape.profile"]["done"], 2)
        self.assertEqual(pipeline["skip_reasons"], [("Outside follower range", 1)])

    def test_sources_yield(self):
        types = {row["source"]: row for row in self.repo.sources()["types"]}
        self.assertEqual(types["similar"]["eligible"], 1)
        self.assertEqual(types["search"]["eligible_rate"], 0.0)

    def test_activity_after_cursor(self):
        events = self.repo.activity()
        self.assertEqual({e["key"] for e in events}, {"seedcreator", "peerone", "bigbrand", "flaky"})
        self.assertEqual(next(e for e in events if e["key"] == "flaky")["state"], "retrying")
        self.assertEqual(self.repo.activity(after=time.time() + 10), [])

    def test_seed_and_retry_actions(self):
        queued = self.repo.seed({"usernames": "@newone, https://www.instagram.com/newtwo/", "queries": "delhi chef"})
        self.assertEqual(queued["queued"], {"scrape.profile": 2, "source.similar": 2, "source.search": 1})
        tasks = TaskStore(self.db)
        tasks.connection.execute("UPDATE tasks SET state='failed', last_error='timed out after 900s' WHERE key='flaky'")
        tasks.connection.commit()
        tasks.close()
        self.assertEqual(self.repo.retry({"reason": "Timed out"}), {"requeued": 1})

    def test_reason_labels(self):
        self.assertEqual(_reason_bucket("ELIGIBILITY:no India/location/language signal; X"), "No India signal")
        self.assertEqual(_reason_bucket("RuntimeError: page 12345 crashed"), "RuntimeError: page N crashed")


if __name__ == "__main__":
    unittest.main()


class DetailShapeTest(RepositoryTest):
    def test_detail_keeps_arrays_the_page_maps_over(self):
        detail = self.repo.creator_detail("peerone")
        for key in ("follower_history", "posts_sample", "provenance", "similar", "warnings", "missing_fields", "reasons"):
            self.assertIsInstance(detail[key], list, key)
        self.assertNotIn("search_text", detail)
