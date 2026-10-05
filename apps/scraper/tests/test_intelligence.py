"""The two signal bugs that corrupted features-v1, pinned down."""

import os
import sqlite3
import tempfile
import unittest

from microindia_scraper.intelligence import (
    FEATURE_VERSION,
    REEL_ANALYSIS_VERSION,
    analyze_reel,
    derive_creator_features,
    language_signals,
    strip_instagram_chrome,
)
from microindia_scraper.models import ContentObservation, ProfileCapture, ProfileObservation
from microindia_scraper.store import CaptureStore

# What Instagram's post page body really looks like (trimmed from a live capture).
PAGE_BODY = """mummastessa
rasikumargalle•Original audio
Follow
#ad

Tide power gel for my regular laundry ❣️
swathi_yashvin
Best👏
1 like
Reply
Get the app
More posts from mummastessa
Meta
About
Blog
English
Afrikaans
العربية
বাংলা
ગુજરાતી
हिन्दी
ಕನ್ನಡ
മലയാളം
मराठी
தமிழ்
తెలుగు
© 2026 Instagram from Meta"""


class LanguageSignalTest(unittest.TestCase):
    def test_footer_language_list_is_not_a_language_signal(self):
        reel = analyze_reel({"content_type": "reel", "caption_text": "Tide power gel for my regular laundry", "text_content": PAGE_BODY})
        self.assertEqual(reel["language_signals"], ["en"])
        self.assertEqual(language_signals(PAGE_BODY), ["en"])
        self.assertNotIn("हिन्दी", strip_instagram_chrome(PAGE_BODY))

    def test_real_indic_caption_is_still_detected(self):
        self.assertEqual(language_signals("आज की आसान रेसिपी 🍲 #food"), ["hi"])
        self.assertEqual(language_signals("இன்று சமையல் #tamil"), ["ta"])
        self.assertEqual(language_signals("Aaj ka recipe बहुत आसान"), ["en", "hi"])

    def test_creator_languages_ignore_page_body(self):
        posts = [{"caption_text": "Weekend brunch in Pune", "text_content": PAGE_BODY} for _ in range(3)]
        features = derive_creator_features({"bio_text": "Food lover"}, posts, {})
        self.assertEqual(features["languages"], ["en"])
        self.assertEqual(features["feature_version"], FEATURE_VERSION)


class TopicWordBoundaryTest(unittest.TestCase):
    def test_substrings_do_not_count_as_topics(self):
        # v1: "ai" in "said"/"chai"/"again", "app" in "happy", "run" in "brunch" -> technology/fitness.
        reel = analyze_reel({"content_type": "reel", "caption_text": "She said chai again, happy brunch with my apple pie"})
        self.assertEqual(reel["topic"], "other")
        self.assertEqual(reel["topic_scores"]["technology"], 0)
        self.assertEqual(reel["topic_scores"]["fitness"], 0)

    def test_whole_words_still_match(self):
        reel = analyze_reel({"content_type": "reel", "caption_text": "Best AI app for coding, my tech setup"})
        self.assertEqual(reel["topic"], "technology")
        self.assertGreaterEqual(reel["topic_scores"]["technology"], 3)

    def test_page_buttons_do_not_fake_a_call_to_action(self):
        reel = analyze_reel({"content_type": "reel", "caption_text": "Morning walk", "text_content": PAGE_BODY})
        self.assertFalse(reel["call_to_action"])
        self.assertEqual(reel["analysis_version"], REEL_ANALYSIS_VERSION)


class FeatureVersionTest(unittest.TestCase):
    def test_materialize_stamps_current_feature_version(self):
        with tempfile.TemporaryDirectory() as directory:
            database = os.path.join(directory, "f.sqlite3")
            store = CaptureStore(database)
            store.create_capture(ProfileCapture(capture_id="c1", candidate_key="instagram:creator",
                                                profile_url="https://www.instagram.com/creator/",
                                                captured_at="2026-10-06T00:00:00Z", schema_version="v"))
            store.save_profile_snapshot(ProfileObservation(capture_id="c1", handle="creator", follower_count=1000), "2026-10-06T00:00:00Z")
            store.save_content_snapshot(ContentObservation(capture_id="c1", content_index=1, platform_content_id="r1",
                                                           permalink="https://www.instagram.com/reel/r1/", content_type="reel",
                                                           caption_text="chai time", text_content=PAGE_BODY), "2026-10-06T00:00:01Z")
            store.materialize_capture_features("c1", "profile-capture-v2-deep-research", "2026-10-06T00:00:02Z")
            versions = {row[0] for row in store.connection.execute("SELECT feature_version FROM post_features UNION SELECT feature_version FROM creator_features")}
            store.close()
        self.assertEqual(versions, {FEATURE_VERSION})


if __name__ == "__main__":
    unittest.main()
