import asyncio
import json
import os
import tempfile
import unittest

from microindia_scraper.e2e import parse_profile_observation
from microindia_scraper.handlers.sourcing import _user_id
from microindia_scraper.runtime import TaskStore
from microindia_scraper.store import CaptureStore


class UserIdTest(unittest.TestCase):
    def test_profile_parser_keeps_numeric_id_only(self):
        base = {"description": "1,234 Followers", "bodyText": "", "headerText": "", "title": "X (@x)"}
        good = parse_profile_observation({**base, "profileId": "7155411787"}, "c1", "https://www.instagram.com/x/", "t")
        bad = parse_profile_observation({**base, "profileId": "abc"}, "c2", "https://www.instagram.com/x/", "t")
        self.assertEqual(good.platform_user_id, "7155411787")
        self.assertIsNone(bad.platform_user_id)

    def test_sourcer_uses_stored_id_before_searching(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite3")
            store = CaptureStore(db)
            store.close()
            tasks = TaskStore(db)
            tasks.connection.execute(
                "INSERT INTO profile_captures(capture_id, candidate_key, profile_url, captured_at, schema_version, status,"
                " requested_content_count) VALUES ('c1', 'k', 'https://www.instagram.com/ani/', '2026-10-06', 'v', 'complete', 18)")
            tasks.connection.execute("INSERT INTO profile_snapshots(capture_id, payload, observed_at) VALUES ('c1', ?, 't')",
                                     (json.dumps({"platform_user_id": "62942439231"}),))
            tasks.connection.commit()

            class NoSearch:
                async def evaluate(self, *args):
                    raise AssertionError("search should not be needed")

            self.assertEqual(asyncio.run(_user_id(NoSearch(), "ani", tasks)), "62942439231")
            tasks.close()


if __name__ == "__main__":
    unittest.main()
