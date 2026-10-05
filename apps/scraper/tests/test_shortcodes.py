import time
import unittest

from microindia_scraper.insights import creator_insights
from microindia_scraper.shortcodes import pk_to_shortcode, permalink_timestamp, shortcode_to_pk, INSTAGRAM_EPOCH_MS


def code_at(ts: float) -> str:
    """A shortcode whose id encodes this creation time (as Instagram's ids do)."""
    return pk_to_shortcode((int(ts * 1000) - INSTAGRAM_EPOCH_MS) << 23)


class ShortcodeTest(unittest.TestCase):
    def test_round_trip_and_bad_input(self):
        self.assertEqual(pk_to_shortcode(shortcode_to_pk("Dd_AFsYydDi")), "Dd_AFsYydDi")
        self.assertIsNone(permalink_timestamp("https://www.instagram.com/someone/"))
        self.assertIsNone(permalink_timestamp(None))

    def test_cadence_uses_post_ids_when_dates_are_missing(self):
        now = time.time()
        posts = [{"permalink": f"https://www.instagram.com/reel/{code_at(now - day * 86400)}/",
                  "like_count": 100, "comment_count": 5, "content_type": "reel"} for day in range(1, 11)]
        insight = creator_insights({"followers": 10_000}, posts, [])
        self.assertIsNotNone(insight["cadence"])
        self.assertAlmostEqual(insight["cadence"]["posts_per_week"], 7.0, delta=0.5)


if __name__ == "__main__":
    unittest.main()
