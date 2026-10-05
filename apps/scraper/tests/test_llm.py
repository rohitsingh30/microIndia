"""llm.py: envelope parsing, cache, retry, usage and argv. The subprocess is always mocked."""

import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from microindia_scraper import llm


def envelope(structured=None, result="", is_error=False):
    body = {"type": "result", "subtype": "success", "is_error": is_error, "result": result,
            "total_cost_usd": 0.01, "modelUsage": {"claude-haiku-4-5": {"outputTokens": 3},
                                                   "claude-sonnet-5-5": {"outputTokens": 40}}}
    if structured is not None:
        body["structured_output"] = structured
    return body


def completed(body, *, returncode=0, stream=False):
    if stream:
        lines = [{"type": "system", "subtype": "init"}, {"type": "assistant", "message": {}}, body]
        stdout = "\n".join(json.dumps(line) for line in lines)
    else:
        stdout = json.dumps(body)
    return mock.Mock(returncode=returncode, stdout=stdout, stderr="")


class LLMBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "llm.sqlite3")
        self.env = mock.patch.dict(os.environ, {"MICROINDIA_CLAUDE_BIN": "/usr/bin/true", "ANTHROPIC_API_KEY": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def usage(self):
        connection = sqlite3.connect(self.db)
        try:
            return {name: count for day, name, count in connection.execute("SELECT * FROM usage") if len(day) == 13}
        finally:
            connection.close()



class LLMTest(LLMBase):
    def test_structured_output_cache_and_usage(self):
        schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
        with mock.patch("microindia_scraper.llm._execute", return_value=completed(envelope({"a": 1}))) as run:
            data, meta = llm.call("hi", schema=schema, purpose="t", database=self.db)
            again, meta2 = llm.call("hi", schema=schema, purpose="t", database=self.db)
        self.assertEqual(data, {"a": 1})
        self.assertEqual(again, {"a": 1})
        self.assertFalse(meta["cached"])
        self.assertTrue(meta2["cached"])
        self.assertEqual(meta["model_id"], "claude-sonnet-5-5")
        self.assertEqual(run.call_count, 1)
        argv = run.call_args[0][0]
        self.assertIn("--json-schema", argv)
        self.assertEqual(argv[argv.index("--output-format") + 1], "json")
        self.assertNotIn("--bare", argv)  # subscription auth: --bare would need an API key
        self.assertEqual(run.call_args.kwargs["input"], "hi")
        usage = self.usage()
        self.assertEqual(usage["llm_calls"], 1)
        self.assertEqual(usage["llm_cache_hits"], 1)
        self.assertEqual(meta["input_hash"], meta2["input_hash"])

    def test_cache_false_runs_again(self):
        with mock.patch("microindia_scraper.llm._execute", return_value=completed(envelope(result='{"x": 2}'))) as run:
            llm.call("p", database=self.db)
            data, meta = llm.call("p", database=self.db, cache=False)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(data, {"x": 2})

    def test_retry_once_then_succeed(self):
        bad = completed(envelope(result="Tool StructuredOutput failed validation", is_error=True), returncode=1)
        good = completed(envelope({"ok": True}))
        with mock.patch("microindia_scraper.llm._execute", side_effect=[bad, good]):
            data, meta = llm.call("p", schema={"type": "object"}, database=self.db)
        self.assertEqual(data, {"ok": True})
        self.assertEqual(meta["attempts"], 2)
        self.assertEqual(self.usage()["llm_failures"], 1)

    def test_timeout_twice_raises(self):
        import subprocess

        with mock.patch("microindia_scraper.llm._execute", side_effect=subprocess.TimeoutExpired("claude", 1)):
            with self.assertRaises(llm.LLMError):
                llm.call("p", database=self.db, timeout=1)
        self.assertEqual(self.usage()["llm_failures"], 2)

    def test_images_use_stream_json_with_base64_blocks(self):
        image = os.path.join(self.tmp.name, "f.jpg")
        with open(image, "wb") as handle:
            handle.write(b"\xff\xd8fakejpeg")
        with mock.patch("microindia_scraper.llm._execute", return_value=completed(envelope({"seen": 1}), stream=True)) as run:
            data, _ = llm.call("look", schema={"type": "object"}, images=[image], database=self.db, model="opus")
        self.assertEqual(data, {"seen": 1})
        argv = run.call_args[0][0]
        self.assertEqual(argv[argv.index("--input-format") + 1], "stream-json")
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        self.assertEqual(argv[argv.index("--model") + 1], "opus")
        message = json.loads(run.call_args.kwargs["input"])
        kinds = [block["type"] for block in message["message"]["content"]]
        self.assertEqual(kinds, ["text", "image", "text"])

    def test_image_bytes_change_the_input_hash(self):
        image = os.path.join(self.tmp.name, "f.jpg")
        with open(image, "wb") as handle:
            handle.write(b"one")
        first = llm.input_hash(model="sonnet", system=None, prompt="p", schema=None, images=[image])
        with open(image, "wb") as handle:
            handle.write(b"two")
        self.assertNotEqual(first, llm.input_hash(model="sonnet", system=None, prompt="p", schema=None, images=[image]))

    def test_bare_only_with_api_key(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            self.assertIn("--bare", llm._argv("sonnet", schema=None, system=None, with_images=False))

    def test_slots_limit_concurrency(self):
        with mock.patch.dict(os.environ, {"MICROINDIA_LLM_SLOTS": "1"}):
            with llm._slot(self.db, wait_seconds=5):
                with self.assertRaises(llm.LLMError):
                    with llm._slot(self.db, wait_seconds=0):
                        pass


class UnavailableAndDeadlineTest(LLMBase):
    def test_failure_classification(self):
        for text in ("Claude AI usage limit reached|1791240000", "API Error: 529 Overloaded", "Not logged in · Please run /login",
                     "connect ECONNREFUSED 1.2.3.4:443", "API Error: Rate limit exceeded"):
            self.assertTrue(llm.classify_failure(text), text)
        self.assertTrue(llm.classify_failure("", api_status=529))
        self.assertFalse(llm.classify_failure("Tool StructuredOutput failed validation"))

    def test_usage_limit_is_not_retried_and_cools_everyone_down(self):
        limit = completed(envelope(result="Claude AI usage limit reached", is_error=True), returncode=1)
        with mock.patch("microindia_scraper.llm._execute", return_value=limit) as run:
            with self.assertRaises(llm.LLMUnavailable):
                llm.call("p", database=self.db)
            self.assertEqual(run.call_count, 1)  # no in-call retry
            self.assertGreater(llm.cooldown_remaining(self.db), 900)
            with self.assertRaises(llm.LLMUnavailable) as caught:  # fails fast without running claude
                llm.call("other prompt", database=self.db)
            self.assertEqual(run.call_count, 1)
            self.assertGreater(caught.exception.retry_after, 0)
        self.assertEqual(self.usage()["llm_unavailable"], 1)

    def test_cache_still_answers_during_cooldown(self):
        with mock.patch("microindia_scraper.llm._execute", return_value=completed(envelope({"a": 1}))):
            llm.call("p", schema={"type": "object"}, database=self.db)
        llm.start_cooldown(self.db, 600)
        data, meta = llm.call("p", schema={"type": "object"}, database=self.db)
        self.assertTrue(meta["cached"])
        self.assertEqual(data, {"a": 1})

    def test_schema_without_structured_output_is_an_error_and_not_cached(self):
        schema = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}
        plain = completed(envelope(result="Here is my analysis in prose."))
        with mock.patch("microindia_scraper.llm._execute", return_value=plain) as run:
            with self.assertRaises(llm.LLMError):
                llm.call("p", schema=schema, database=self.db)
        self.assertEqual(run.call_count, 2)  # retried once, then gave up
        connection = sqlite3.connect(self.db)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0], 0)
        connection.close()
        wrong_keys = completed(envelope(result='{"other": 1}'))
        with mock.patch("microindia_scraper.llm._execute", return_value=wrong_keys):
            with self.assertRaises(llm.LLMError):
                llm.call("p2", schema=schema, database=self.db)
        text_json = completed(envelope(result='{"summary": "ok"}'))  # JSON text with the required keys is fine
        with mock.patch("microindia_scraper.llm._execute", return_value=text_json):
            data, _ = llm.call("p3", schema=schema, database=self.db)
        self.assertEqual(data, {"summary": "ok"})

    def test_deadline_caps_attempts_and_skips_a_late_retry(self):
        clock = [1000.0]
        bad = completed(envelope(result="Tool StructuredOutput failed validation", is_error=True), returncode=1)

        def slow_failure(argv, *, input, timeout):
            clock[0] += 50
            return bad

        with mock.patch("microindia_scraper.llm.time.time", side_effect=lambda: clock[0]), \
                mock.patch("microindia_scraper.llm._execute", side_effect=slow_failure) as run:
            with self.assertRaises(llm.LLMError):
                llm.call("p", database=self.db, timeout=420, deadline=1100.0)
        self.assertEqual(run.call_count, 1)  # 50 s left after the first attempt: no retry
        self.assertLessEqual(run.call_args.kwargs["timeout"], 100.0)  # attempt cut to the time left
        with mock.patch("microindia_scraper.llm._execute") as never:
            with self.assertRaises(llm.LLMError):
                llm.call("q", database=self.db, deadline=__import__("time").time() + 30)
        never.assert_not_called()

    def test_timeout_kills_the_whole_process_group(self):
        import subprocess
        import time as _time

        started = _time.time()
        with self.assertRaises(subprocess.TimeoutExpired):
            llm._execute(["/bin/sh", "-c", "sleep 30 & sleep 30"], input="", timeout=0.5)
        self.assertLess(_time.time() - started, 10)

    def test_brand_chat_never_waits_long_for_a_slot(self):
        with mock.patch.dict(os.environ, {"MICROINDIA_LLM_SLOTS": "1"}):
            with llm._slot(self.db, wait_seconds=5):
                with mock.patch("microindia_scraper.llm._execute") as run:
                    started = __import__("time").time()
                    with self.assertRaises(llm.LLMError):
                        llm.call("p", database=self.db, retries=0, slot_wait=0.5)
                    self.assertLess(__import__("time").time() - started, 3)
                    run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
