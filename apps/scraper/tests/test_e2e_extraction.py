import unittest

from microindia_scraper.e2e import (
    content_capture_skip_reasons,
    parse_content_observation,
    parse_profile_observation,
    quality_gate,
)
from microindia_scraper.intelligence import extract_ai_evidence

class ExtractionTests(unittest.TestCase):
    def test_profile_parser_keeps_research_signals_separate(self):
        profile = parse_profile_observation(
            {
                "title": "Asha (@asha)",
                "description": "20K Followers, 500 Following, 180 Posts - Asha on Instagram",
                "headerText": "Asha\n20K followers\n500 following\n180 posts\n📍 Mumbai, India\nFood creator\nDM for collab",
                "bodyText": "Asha recipe creator Hindi food creator",
                "structuredProfile": {"name": "Asha", "description": "Food creator in Mumbai, India"},
                "externalUrl": "https://example.com",
                "isPrivate": False,
                "isVerified": True,
            },
            "capture-1",
            "https://www.instagram.com/asha/",
            "2026-09-27T00:00:00+00:00",
        )

        self.assertEqual(profile.follower_count, 20_000)
        self.assertEqual(profile.following_count, 500)
        self.assertEqual(profile.post_count, 180)
        self.assertEqual(profile.location_text, "Mumbai, India")
        self.assertEqual(profile.business_category, "food")
        self.assertIn("en", profile.language_signals)
        self.assertTrue(profile.commercial_signals["has_business_contact_prompt"])
        self.assertTrue(profile.is_verified)

    def test_content_parser_extracts_caption_metrics_and_context(self):
        content = parse_content_observation(
            {
                "description": "12.5K likes, 120 comments - asha: Easy Kerala recipe #food @brand",
                "text": "12.5K likes 120 comments 90K views",
                "articleText": "Pinned collaboration",
                "caption": None,
                "publishedAt": "2026-09-20T10:30:00+00:00",
                "locationText": "Kerala",
                "isPinned": True,
                "isCollaboration": True,
                "type": "reel",
            },
            "capture-1",
            1,
            "https://www.instagram.com/reel/abc123/",
            "2026-09-27T00:00:00+00:00",
        )

        self.assertEqual(content.caption_text, "Easy Kerala recipe #food @brand")
        self.assertEqual(content.like_count, 12_500)
        self.assertEqual(content.comment_count, 120)
        self.assertEqual(content.view_count, 90_000)
        self.assertEqual(content.published_at, "2026-09-20T10:30:00+00:00")
        self.assertEqual(content.location_text, "Kerala")
        self.assertTrue(content.is_pinned)
        self.assertTrue(content.is_collaboration)
        self.assertEqual(content.hashtags, ["#food"])
        self.assertEqual(content.mentions, ["@brand"])
        self.assertEqual(content.metric_availability, "full")


    def test_ai_evidence_is_explicit_and_unknown_without_markers(self):
        positive = extract_ai_evidence("A portrait generated with AI in Midjourney")
        self.assertEqual(positive["ai_label"], "ai_generated")
        self.assertGreaterEqual(positive["ai_score"], 0.75)
        self.assertEqual(extract_ai_evidence("Real home cooking in Mumbai")["ai_label"], "unknown")
        mixed = extract_ai_evidence("AI art tutorial for #ai creators")
        self.assertEqual(mixed["ai_label"], "ai_signal")


    def test_pre_content_gate_skips_private_and_out_of_scope_profiles(self):
        private = parse_profile_observation(
            {
                "structuredProfile": {"description": "Mumbai food creator"},
                "bodyText": "Private account",
                "isPrivate": True,
            },
            "capture-private",
            "https://www.instagram.com/private/",
            "2026-09-27T00:00:00+00:00",
        )
        self.assertIn("private account", content_capture_skip_reasons(private))

        out_of_scope = parse_profile_observation(
            {
                "structuredProfile": {"description": "Mumbai travel creator"},
                "bodyText": "2.5M followers",
                "description": "2.5M followers",
                "isPrivate": False,
            },
            "capture-large",
            "https://www.instagram.com/large/",
            "2026-09-27T00:00:00+00:00",
        )
        reasons = content_capture_skip_reasons(out_of_scope, requested_niche="food creator")
        self.assertIn("outside follower range (500-1M)", reasons)
        # Niche is not a gate: a travel creator found via a food search is still worth capturing.
        self.assertFalse(any(reason.startswith("category mismatch") for reason in reasons))

    def test_in_band_profile_without_bio_location_still_gets_deep_capture(self):
        quiet_bio = parse_profile_observation(
            {"structuredProfile": {"description": "eat. travel. repeat"}, "bodyText": "45K followers",
             "description": "45K followers", "isPrivate": False},
            "capture-quiet", "https://www.instagram.com/quiet/", "2026-10-05T00:00:00+00:00",
        )
        self.assertEqual(content_capture_skip_reasons(quiet_bio, requested_niche="food blogger"), [])

    def test_quality_gate_only_rejects_on_hard_facts(self):
        profile = {
            "handle": "asha",
            "follower_count": 20_000,
            "bio_text": "Food creator",
            "location_text": "Mumbai, India",
            "ai_label": "unknown",
            "ai_signals": [],
        }
        content = [{"item_status": "observed", "ai_score": 0.0} for _ in range(8)]
        quality = {"completeness_score": 65.0}
        self.assertTrue(quality_gate(profile, content, quality)["eligible"])
        # Few posts, no location, AI evidence: notes for filtering, never a rejection.
        few = quality_gate(dict(profile, location_text=None), content[:3], {"completeness_score": 40.0})
        self.assertTrue(few["eligible"])
        self.assertIn("FEW_POSTS:3", few["notes"])
        # AI personas are a hard rejection.
        explicit = dict(profile, ai_label="ai_dominant", ai_signals=["profile_ai_creator"])
        self.assertIn("AI_PROFILE_DOMINANT", quality_gate(explicit, content, quality)["reasons"])
        repeated = [dict(item, ai_score=0.95) for item in content[:3]] + content[3:]
        self.assertIn("AI_CONTENT_REPEATED:3/8", quality_gate(profile, repeated, quality)["reasons"])
        # Hard facts still reject.
        self.assertFalse(quality_gate(profile, content, quality, ["private account"])["eligible"])
        self.assertFalse(quality_gate(dict(profile, follower_count=None), content, quality)["eligible"])
if __name__ == "__main__":
    unittest.main()


class PostCountsFromDescriptionTest(unittest.TestCase):
    def test_comment_likes_in_body_do_not_override_post_counts(self):
        from microindia_scraper.e2e import parse_content_observation

        observation = parse_content_observation(
            {
                "description": '2,345 likes, 45 comments - creator on September 20, 2026: "Pandal tour"',
                "text": "someone 1w Wow 1 like Reply",
                "articleText": "",
            },
            "capture", 1, "https://www.instagram.com/p/abc/", "2026-10-05T00:00:00Z",
        )
        self.assertEqual((observation.like_count, observation.comment_count), (2345, 45))


class ProfileHeaderTest(unittest.TestCase):
    """Real header text captured from Instagram profile pages (Oct 2026 layout)."""

    def test_business_label_is_detected(self):
        from microindia_scraper.e2e import parse_header

        header = ("63degreeshyd\n63 Degrees Hyderabad\n493 posts\n11.7K followers\n0 following\nBuffet Restaurant\n"
                  "🍽️ Modern Regional Buffet\n📞 9733386333\nmaps.app.goo.gl/abc and 2 more\nMangoMia\nDineLight")
        parsed = parse_header(header, "63degreeshyd", "63 Degrees Hyderabad")
        self.assertEqual(parsed["category_label"], "Buffet Restaurant")
        self.assertTrue(parsed["is_business_label"])
        self.assertNotIn("MangoMia", parsed["bio"])  # story highlights are not bio

    def test_creator_bio_and_label(self):
        from microindia_scraper.e2e import parse_header, parse_profile_observation
        from microindia_scraper.eligibility import human_indian_micro_creator_reasons

        header = ("foodie_sisterss_\nfoodie sisters | Food blogger |📍 Mumbai | 🇮🇳\n746 posts\n54.4K followers\n4,188 following\n"
                  "Blogger\n⇢ FSSAI Certified | Mumbai 🇮🇳\n🍽️ Food • Luxury Dining • Recipes\n🤝 Brand Partnerships ↓\nmore\n"
                  "www.facebook.com/share/x and 3 more")
        parsed = parse_header(header, "foodie_sisterss_", None)
        self.assertEqual(parsed["category_label"], "Blogger")
        self.assertFalse(parsed["is_business_label"])
        self.assertIn("Brand Partnerships", parsed["bio"])
        profile = parse_profile_observation(
            {"headerText": header, "description": "54.4K Followers, 4,188 Following, 746 Posts - See Instagram photos"},
            "c", "https://www.instagram.com/foodie_sisterss_/", "t")
        self.assertIn("FSSAI Certified", profile.bio_text)
        self.assertEqual(human_indian_micro_creator_reasons(profile), [])
