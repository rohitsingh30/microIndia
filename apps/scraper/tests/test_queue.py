import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from microindia_scraper.queue import execute_job, main, worker_once
from microindia_scraper.store import CaptureStore


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp_dir.name) / "queue.sqlite3")
        self.store = CaptureStore(self.database)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    def test_dry_run_leases_and_completes_without_browser(self):
        self.store.enqueue_job("capture_creator_profile", "profile:dry", {"profile_url": "https://www.instagram.com/demo/"})
        self.store.close()
        self.assertTrue(worker_once(self.database, "worker-a", dry_run=True))
        store = CaptureStore(self.database)
        row = store.connection.execute("SELECT status FROM collection_jobs").fetchone()
        attempt = store.connection.execute("SELECT outcome FROM collection_attempts").fetchone()
        self.assertEqual(row["status"], "complete")
        self.assertEqual(attempt["outcome"], "dry_run")
        store.close()

    def test_execute_job_dispatches_profile_capture(self):
        job = {"job_type": "capture_creator_profile", "payload": {"candidate_key": "instagram:demo", "profile_url": "https://www.instagram.com/demo/", "public_verified_at": "2026-09-27T00:00:00Z", "source_name": "approved-index"}}
        async def fake_capture(*args):
            return {"status": "complete"}

        with patch("microindia_scraper.queue.run_capture", side_effect=fake_capture):
            result = execute_job(job, database="db.sqlite3", user_data_dir="/tmp/worker-a", profile_directory="Default")
        self.assertEqual(result["status"], "complete")

    def test_shared_profile_is_rejected_without_cdp(self):
        self.store.enqueue_job("capture_creator_profile", "profile:shared-rejected", {"profile_url": "https://www.instagram.com/demo/"})
        self.store.close()
        self.assertTrue(worker_once(self.database, "worker-shared", user_data_dir="/Users/rohit/playwright-chrome-profile-4"))
        store = CaptureStore(self.database)
        row = store.connection.execute("SELECT status, last_error FROM collection_jobs").fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertIn("cannot be used by queue workers directly", row["last_error"])
        store.close()

    def test_shared_cdp_requires_page_target(self):
        self.store.enqueue_job("capture_creator_profile", "profile:shared-no-page", {"profile_url": "https://www.instagram.com/demo/"})
        self.store.close()
        self.assertTrue(worker_once(self.database, "worker-cdp", cdp_url="http://127.0.0.1:9222"))
        store = CaptureStore(self.database)
        row = store.connection.execute("SELECT status, last_error FROM collection_jobs").fetchone()
        self.assertEqual(row["status"], "queued")
        self.assertIn("No available shared browser page", row["last_error"])
        store.close()

    def test_shared_cdp_worker_leases_and_releases_page(self):
        self.store.register_browser_page("target-a", "owner-a")
        self.store.enqueue_job("capture_creator_profile", "profile:shared-page", {"profile_url": "https://www.instagram.com/demo/"})
        self.store.close()
        with patch("microindia_scraper.queue.execute_job", return_value={"status": "complete"}) as execute:
            self.assertTrue(worker_once(self.database, "worker-cdp", cdp_url="http://127.0.0.1:9222"))
        execute.assert_called_once()
        self.assertEqual(execute.call_args.kwargs["page_target_id"], "target-a")
        store = CaptureStore(self.database)
        page = store.connection.execute("SELECT status, leased_by FROM browser_pages").fetchone()
        self.assertEqual(page["status"], "available")
        self.assertIsNone(page["leased_by"])
        store.close()

    def test_manual_auth_failure_is_terminal_and_visible(self):
        self.store.enqueue_job("capture_creator_profile", "profile:auth", {"profile_url": "https://www.instagram.com/demo/"})
        self.store.close()
        with patch("microindia_scraper.queue.execute_job", side_effect=RuntimeError("Instagram login challenge requires verification")):
            self.assertTrue(worker_once(self.database, "worker-auth", user_data_dir="/tmp/worker-auth"))
        store = CaptureStore(self.database)
        job = store.connection.execute("SELECT status, last_error FROM collection_jobs").fetchone()
        attempt = store.connection.execute("SELECT outcome FROM collection_attempts").fetchone()
        self.assertEqual(job["status"], "needs_manual_auth")
        self.assertEqual(attempt["outcome"], "needs_manual_auth")
        health = store.connection.execute("SELECT status FROM account_health WHERE account_alias = 'default'").fetchone()
        self.assertEqual(health["status"], "needs_manual_auth")
        store.close()

    def test_retryable_failure_is_requeued(self):
        self.store.enqueue_job("capture_creator_profile", "profile:retry", {"profile_url": "https://www.instagram.com/demo/"})
        self.store.close()
        with patch("microindia_scraper.queue.execute_job", side_effect=TimeoutError("temporary timeout")):
            self.assertTrue(worker_once(self.database, "worker-retry", user_data_dir="/tmp/worker-retry"))
        store = CaptureStore(self.database)
        job = store.connection.execute("SELECT status, available_at FROM collection_jobs").fetchone()
        self.assertEqual(job["status"], "queued")
        self.assertGreater(job["available_at"], 0)
        health = store.connection.execute("SELECT status FROM account_health WHERE account_alias = 'default'").fetchone()
        self.assertEqual(health["status"], "cooldown")
        store.close()

    def test_capture_result_needs_manual_auth_is_terminal(self):
        self.store.enqueue_job("capture_creator_profile", "profile:result-auth", {"profile_url": "https://www.instagram.com/demo/"})
        self.store.close()
        with patch("microindia_scraper.queue.execute_job", return_value={"status": "needs_manual_auth"}):
            self.assertTrue(worker_once(self.database, "worker-result-auth", user_data_dir="/tmp/worker-result-auth"))
        store = CaptureStore(self.database)
        row = store.connection.execute("SELECT status FROM collection_jobs").fetchone()
        self.assertEqual(row["status"], "needs_manual_auth")
        store.close()

    def test_execute_job_rejects_unsupported_type(self):
        with self.assertRaises(ValueError):
            execute_job({"job_type": "post_enrichment", "payload": {}}, database="db.sqlite3", user_data_dir="/tmp/worker-a", profile_directory="Default")


    def test_zero_max_jobs_keeps_polling_until_interrupted(self):
        with patch.object(sys, "argv", ["queue", "--dry-run", "--max-jobs", "0", "--poll-seconds", "0"]), \
             patch("microindia_scraper.queue.worker_once", side_effect=[False, KeyboardInterrupt]), \
             patch("microindia_scraper.queue.time.sleep"):
            with self.assertRaises(KeyboardInterrupt):
                main()

    def test_once_exits_after_one_iteration(self):
        with patch.object(sys, "argv", ["queue", "--dry-run", "--once"]), \
             patch("microindia_scraper.queue.worker_once", return_value=False) as worker:
            main()
        self.assertEqual(worker.call_count, 1)
if __name__ == "__main__":
    unittest.main()