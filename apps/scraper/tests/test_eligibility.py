import unittest

from microindia_scraper.eligibility import is_human_indian_micro_creator
from microindia_scraper.models import ProfileObservation


class EligibilityTests(unittest.TestCase):
    def test_accepts_indian_human_micro_creator(self):
        profile = ProfileObservation(
            capture_id="capture-1",
            handle="heenam_jain",
            display_name="Heena Jain",
            bio_text="Delhi food creator sharing home recipes",
            follower_count=85_000,
            is_private=False,
            location_text="Delhi, India",
            language_signals=["Hindi"],
        )
        self.assertTrue(is_human_indian_micro_creator(profile))

    def test_rejects_company_channel_even_if_micro_sized(self):
        profile = ProfileObservation(
            capture_id="capture-2",
            handle="indiafoodnetwork",
            display_name="India Food Network",
            bio_text="Official Indian food media channel",
            follower_count=80_000,
            is_private=False,
            location_text="India",
        )
        self.assertFalse(is_human_indian_micro_creator(profile))

    def test_rejects_large_creator(self):
        profile = ProfileObservation(
            capture_id="capture-3",
            handle="creator",
            display_name="Indian Creator",
            bio_text="Lifestyle creator in Mumbai",
            follower_count=2_500_000,
            is_private=False,
            location_text="Mumbai, India",
        )
        self.assertFalse(is_human_indian_micro_creator(profile))  # beyond the 500-1M scrape range


if __name__ == "__main__":
    unittest.main()

class IndiaEvidenceFromPostsTest(unittest.TestCase):
    def _profile(self, **overrides):
        from microindia_scraper.models import ProfileObservation

        values = dict(capture_id="c", handle="eatrepeat", display_name="Eat Repeat", follower_count=45_000,
                      bio_text="eat. travel. repeat", language_signals=["en"])
        values.update(overrides)
        return ProfileObservation(**values)

    def test_missing_india_evidence_never_rejects_but_posts_supply_it(self):
        from microindia_scraper.eligibility import human_indian_micro_creator_reasons, india_signals

        self.assertEqual(human_indian_micro_creator_reasons(self._profile()), [])
        self.assertEqual(india_signals(self._profile()), [])
        self.assertTrue(india_signals(self._profile(), "Best biryani in town #hyderabadfood Charminar"))

    def test_small_creators_are_in_band_now(self):
        from microindia_scraper.eligibility import human_indian_micro_creator_reasons

        self.assertEqual(human_indian_micro_creator_reasons(self._profile(follower_count=2_500)), [])
        self.assertEqual(human_indian_micro_creator_reasons(self._profile(follower_count=600)), [])
        self.assertTrue(human_indian_micro_creator_reasons(self._profile(follower_count=300)))

    def test_indic_script_rupee_and_language_codes_count(self):
        from microindia_scraper.eligibility import human_indian_micro_creator_reasons, india_evidence

        self.assertIn("indic script", india_evidence("स्वादिष्ट खाना"))
        self.assertIn("rupee/+91", india_evidence("Thali for ₹199"))
        self.assertEqual(human_indian_micro_creator_reasons(self._profile(language_signals=["en", "ta"])), [])


class AiAndRepostGuardrailTest(unittest.TestCase):
    def test_ai_personas_and_repost_pages_are_rejected(self):
        from microindia_scraper.eligibility import _plain, ai_or_repost_reason

        self.assertEqual(ai_or_repost_reason("indian.ai.creator", "Indian AI Creator", ""), "AI-generated persona")
        self.assertEqual(ai_or_repost_reason("aigirl.priya", "Priya", ""), "AI-generated persona")
        self.assertEqual(ai_or_repost_reason("x", "Virtual Influencer Maya", ""), "AI-generated persona")
        self.assertEqual(ai_or_repost_reason("foodpage", "Food", _plain("🔸ᴅᴍ ꜰᴏʀ ᴄʀᴇᴅɪᴛ/ʀᴇᴍᴏᴠᴇ")), "repost/aggregator page")

    def test_real_names_containing_ai_are_kept(self):
        from microindia_scraper.eligibility import ai_or_repost_reason

        for handle, name in [("chai.lover", "Chai Lover"), ("aishwarya_eats", "Aishwarya"), ("sai.creator", "Sai"),
                             ("asha", "Asha | brand partnerships")]:
            self.assertEqual(ai_or_repost_reason(handle, name, "DM for collabs"), "", handle)

    def test_ai_dominant_profile_is_rejected(self):
        from microindia_scraper.eligibility import human_indian_micro_creator_reasons
        from microindia_scraper.models import ProfileObservation

        profile = ProfileObservation(capture_id="c", handle="maya", follower_count=20_000, ai_label="ai_dominant")
        self.assertIn("AI-generated persona", human_indian_micro_creator_reasons(profile))


class SmallBusinessTest(unittest.TestCase):
    def _p(self, **kw):
        from microindia_scraper.models import ProfileObservation

        return ProfileObservation(capture_id="c", **kw)

    def test_small_business_kept_big_business_and_publishers_dropped(self):
        from microindia_scraper.eligibility import account_kind, human_indian_micro_creator_reasons as reasons

        shop = self._p(handle="aggarwal_namkeen", account_type="business", follower_count=11_000)
        self.assertEqual((account_kind(shop), reasons(shop)), ("business", []))
        bakery = self._p(handle="home.bakes", bio_text="Home bakery | DM to order", follower_count=3_000)
        self.assertEqual(reasons(bakery), [])
        chain = self._p(handle="bigchain", account_type="business", follower_count=155_000)
        self.assertTrue(any("big business" in r for r in reasons(chain)))
        news = self._p(handle="citynews", display_name="City News", bio_text="Daily news", follower_count=30_000)
        self.assertTrue(any("publisher" in r for r in reasons(news)))
        ai = self._p(handle="maya", account_type="AI-generated profile", follower_count=20_000)
        self.assertIn("AI-generated persona", reasons(ai))
