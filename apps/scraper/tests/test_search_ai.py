import os
import unittest
from unittest import mock

from microindia_scraper.search_ai import chat_search, parse_rules


class ChatSearchRulesTest(unittest.TestCase):
    def test_request_becomes_criteria(self):
        criteria, _ = parse_rules("Hyderabad food creators under 50K who've done brand deals")
        self.assertEqual(criteria, {"city": "Hyderabad", "category": "food", "max_followers": "50000", "brand": "1"})

    def test_follow_up_refines_and_reset_clears(self):
        current = {"city": "Hyderabad", "category": "food"}
        refined, _ = parse_rules("only ones posting recently", current)
        self.assertEqual(refined["active_days"], "30")
        self.assertEqual(refined["city"], "Hyderabad")
        fresh, _ = parse_rules("start over, fashion creators in mumbai", current)
        self.assertEqual(fresh, {"city": "Mumbai", "category": "fashion"})

    def test_ranges_languages_engagement_and_niche(self):
        criteria, _ = parse_rules("telugu creators between 5k and 20k with 3% engagement who post about biryani")
        self.assertEqual(criteria["min_followers"], "5000")
        self.assertEqual(criteria["max_followers"], "20000")
        self.assertEqual(criteria["language"], "te")
        self.assertEqual(criteria["min_engagement"], "0.03")
        self.assertEqual(criteria["q"], "biryani")

    def test_llm_engine_used_when_configured_and_falls_back_on_failure(self):
        good = mock.Mock(returncode=0, stdout='noise {"city": "Pune", "category": "food", "reply": "Pune food creators."} noise')
        with mock.patch.dict(os.environ, {"MICROINDIA_LLM_CMD": "opencode run"}), mock.patch("subprocess.run", return_value=good):
            answer = chat_search("food creators in pune")
        self.assertEqual(answer["engine"], "llm")
        self.assertEqual(answer["criteria"], {"city": "Pune", "category": "food"})
        broken = mock.Mock(returncode=1, stdout="")
        with mock.patch.dict(os.environ, {"MICROINDIA_LLM_CMD": "opencode run"}), mock.patch("subprocess.run", return_value=broken):
            answer = chat_search("food creators in pune")
        self.assertEqual(answer["engine"], "rules")
        self.assertEqual(answer["criteria"]["city"], "Pune")


if __name__ == "__main__":
    unittest.main()
