import json
import os
import tempfile
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
        envelope = {"type": "result", "is_error": False, "subtype": "success", "result": "{}",
                    "structured_output": {"city": "Pune", "category": "food", "reply": "Pune food creators."}}
        good = mock.Mock(returncode=0, stdout=json.dumps(envelope), stderr="")
        with tempfile.TemporaryDirectory() as directory:
            env = {"MICROINDIA_LLM": "on", "MICROINDIA_CLAUDE_BIN": "/bin/echo",
                   "MICROINDIA_DATABASE": os.path.join(directory, "llm.sqlite3")}
            with mock.patch.dict(os.environ, env), mock.patch("microindia_scraper.llm._execute", return_value=good) as run:
                answer = chat_search("food creators in pune")
            self.assertEqual(answer["engine"], "llm")
            self.assertEqual(answer["criteria"], {"city": "Pune", "category": "food"})
            self.assertIn("--json-schema", run.call_args[0][0])
            broken = mock.Mock(returncode=1, stdout="", stderr="boom")
            with mock.patch.dict(os.environ, env), mock.patch("microindia_scraper.llm._execute", return_value=broken):
                answer = chat_search("food creators in delhi")
            self.assertEqual(answer["engine"], "rules")
            self.assertEqual(answer["criteria"]["city"], "Delhi")

    def test_llm_is_off_unless_switched_on(self):
        with mock.patch.dict(os.environ, {"MICROINDIA_LLM": "", "MICROINDIA_LLM_CMD": ""}), mock.patch("microindia_scraper.llm._execute") as run:
            answer = chat_search("food creators in pune")
        self.assertEqual(answer["engine"], "rules")
        run.assert_not_called()

if __name__ == "__main__":
    unittest.main()
