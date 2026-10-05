import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from microindia_scraper.browser_health import check_health
from microindia_scraper.store import CaptureStore


class BrowserHealthTests(unittest.TestCase):
    def test_reports_healthy_when_cdp_and_registered_pages_are_available(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "health.sqlite3")
            store = CaptureStore(database)
            store.register_browser_page("target-a", "owner-a")
            store.heartbeat_browser_owner("owner-a", status="healthy", minimum_pages=1, page_count=1)
            store.close()
            with patch("microindia_scraper.browser_health._fetch_json", side_effect=[
                {"Browser": "Chrome/Test", "Protocol-Version": "1.3"},
                [{"id": "target-a", "type": "page", "title": "Instagram", "url": "https://instagram.com/"}],
            ]):
                result = check_health("http://127.0.0.1:9222", database)
            self.assertEqual(result["status"], "healthy")
            self.assertTrue(result["checks"]["cdp_minimum_pages"])

    def test_reports_unavailable_when_cdp_is_down(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "health.sqlite3")
            CaptureStore(database).close()
            with patch("microindia_scraper.browser_health._fetch_json", side_effect=OSError("connection refused")):
                result = check_health("http://127.0.0.1:9222", database)
            self.assertEqual(result["status"], "unavailable")
            self.assertFalse(result["checks"]["cdp_reachable"])


if __name__ == "__main__":
    unittest.main()