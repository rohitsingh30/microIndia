import sqlite3
import asyncio
import json
import os
import tempfile
import time
import unittest

from microindia_scraper import handlers
from microindia_scraper.handlers.common import mentioned_usernames, username_of
from microindia_scraper.runtime import AuthBlocked, Done, Fail, FollowUp, Retry, Skip, TaskStore, handler
from microindia_scraper.runtime.browser import BrowserSession, TabPool
from microindia_scraper.runtime.registry import TaskContext, handlers_for
from microindia_scraper.runtime.results import AuthRequired
from microindia_scraper.runtime.runner import Runner


class FakePage:
    def __init__(self, target_id):
        self.target_id = target_id
        self.url = "about:blank"

    async def goto(self, url):
        self.url = url

    async def get_url(self):
        return self.url

    async def evaluate(self, script, *args):
        return "{}"


class FakeBrowser:
    def __init__(self):
        self.pages = []
        self.counter = 0

    async def start(self):
        return None

    async def stop(self):
        return None

    async def new_page(self, url=None):
        self.counter += 1
        page = FakePage(f"T{self.counter}")
        self.pages.append(page)
        return page

    async def get_pages(self):
        return list(self.pages)

    async def close_page(self, page):
        self.pages = [item for item in self.pages if item is not page]


def fake_session():
    browser = FakeBrowser()
    return BrowserSession("http://fake", browser_factory=lambda: browser), browser


CALLS = []


@handler("test.source", needs_page=True)
async def _test_source(ctx, task):
    CALLS.append(("source", task["key"], ctx.page.target_id))
    return Done({"found": 2}, follow_ups=[FollowUp("test.scrape", "a"), FollowUp("test.scrape", "b")])


@handler("test.scrape", needs_page=True)
async def _test_scrape(ctx, task):
    CALLS.append(("scrape", task["key"], ctx.page.target_id))
    if task["key"] == "boom":
        raise RuntimeError("tab crashed")
    if task["key"] == "wall":
        raise AuthRequired("login wall")
    return Done({"eligible": True})


class TaskStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "t.sqlite3")
        self.store = TaskStore(self.db, max_attempts=3)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_enqueue_dedupes_by_kind_and_key(self):
        self.assertTrue(self.store.enqueue("scrape.profile", "alice", {"username": "alice"}))
        self.assertFalse(self.store.enqueue("scrape.profile", "alice", {"username": "alice"}))
        self.assertTrue(self.store.enqueue("source.similar", "alice"))
        self.assertEqual(self.store.counts()["scrape.profile"], {"queued": 1})

    def test_lease_respects_kinds_priority_and_run_at(self):
        self.store.enqueue("a.x", "low", priority=0)
        self.store.enqueue("a.x", "high", priority=5)
        self.store.enqueue("a.x", "later", priority=9, delay_seconds=3600)
        self.store.enqueue("b.y", "other", priority=10)
        self.assertEqual(self.store.lease("r", ["a.x"])["key"], "high")
        self.assertEqual(self.store.lease("r", ["a.x"])["key"], "low")
        self.assertIsNone(self.store.lease("r", ["a.x"]))

    def test_done_enqueues_follow_ups_once(self):
        self.store.enqueue("s", "q")
        task = self.store.lease("r", ["s"])
        state = self.store.apply(task, "r", Done({"n": 2}, [FollowUp("p", "x"), FollowUp("p", "x"), FollowUp("p", "y")]))
        self.assertEqual(state, "done")
        self.assertEqual(self.store.counts()["p"], {"queued": 2})
        child = self.store.lease("r", ["p"])
        self.assertEqual(child["parent_task_id"], task["task_id"])

    def test_retry_backs_off_then_fails_at_cap(self):
        self.store.enqueue("k", "t")
        now = time.time()
        for attempt in range(1, 4):
            task = self.store.lease("r", ["k"], now=now)
            self.assertEqual(task["attempts"], attempt)
            state = self.store.apply(task, "r", Retry("flaky"), now=now)
            now += 7200
        self.assertEqual(state, "failed")
        row = self.store.connection.execute("SELECT state, last_error FROM tasks").fetchone()
        self.assertIn("gave up after 3 attempts", row["last_error"])

    def test_retry_is_not_visible_until_run_at(self):
        self.store.enqueue("k", "t")
        task = self.store.lease("r", ["k"])
        self.store.apply(task, "r", Retry("later", after_seconds=60))
        self.assertIsNone(self.store.lease("r", ["k"]))
        self.assertIsNotNone(self.store.lease("r", ["k"], now=time.time() + 61))

    def test_auth_block_parks_task_refunds_attempt_and_clear_requeues(self):
        self.store.enqueue("k", "t")
        task = self.store.lease("r", ["k"])
        self.assertEqual(self.store.apply(task, "r", AuthBlocked("login wall")), "auth_blocked")
        self.assertEqual(self.store.auth_blocked(), "login wall")
        self.assertEqual(self.store.clear_auth_blocked(), 1)
        self.assertIsNone(self.store.auth_blocked())
        again = self.store.lease("r", ["k"])
        self.assertEqual(again["attempts"], 1)

    def test_expired_lease_is_released_then_failed_at_cap(self):
        self.store.enqueue("k", "t")
        now = time.time()
        # Each holder dies holding the task; another runner picks the expired lease up.
        for holder in ("r1", "r2", "r3"):
            self.assertIsNotNone(self.store.lease(holder, ["k"], lease_seconds=10, now=now))
            now += 11
        self.assertIsNone(self.store.lease("r4", ["k"], now=now))
        self.assertEqual(self.store.counts()["k"], {"failed": 1})

    def test_release_leases_on_restart(self):
        self.store.enqueue("k", "t")
        self.store.lease("runner-1", ["k"])
        self.assertEqual(self.store.release_leases("runner-1"), 1)
        self.assertIsNotNone(self.store.lease("runner-1", ["k"]))

    def test_stale_owner_cannot_apply(self):
        self.store.enqueue("k", "t", now=time.time() - 100)
        task = self.store.lease("r1", ["k"], lease_seconds=1, now=time.time() - 10)
        self.store.lease("r2", ["k"])
        self.assertEqual(self.store.apply(task, "r1", Done()), "lost")

    def test_revisit_done_filters_on_result(self):
        for key, eligible in (("yes", True), ("no", False)):
            self.store.enqueue("scrape.profile", key)
            task = self.store.lease("r", ["scrape.profile"])
            self.store.apply(task, "r", Done({"eligible": eligible}), now=time.time() - 100000)
        revisited = self.store.revisit_done(
            "scrape.profile", older_than_seconds=86400, where_json="json_extract(result, '$.eligible') = 1"
        )
        self.assertEqual(revisited, 1)
        self.assertEqual(self.store.lease("r", ["scrape.profile"])["key"], "yes")


class RunnerTest(unittest.TestCase):
    def setUp(self):
        CALLS.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "t.sqlite3")
        self.store = TaskStore(self.db)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _runner(self, kinds, tabs=2, max_tasks=0):
        session, browser = fake_session()
        runner = Runner(tasks=self.store, session=session, kinds=kinds, tabs=tabs, database=self.db,
                        runner_id="test-runner", poll_seconds=0.01, housekeeping_seconds=0.01,
                        max_tasks=max_tasks)
        return runner, browser

    def test_source_feeds_scrape_through_follow_ups(self):
        self.store.enqueue("test.source", "q")
        runner, _ = self._runner(handlers_for(["test.*"]), max_tasks=3)
        asyncio.run(asyncio.wait_for(runner.run(), 5))
        self.assertEqual(sorted(key for _, key, _ in CALLS), ["a", "b", "q"])
        self.assertEqual(self.store.counts()["test.scrape"], {"done": 2})

    def test_runner_only_takes_its_kinds(self):
        self.store.enqueue("test.source", "q")
        self.store.enqueue("test.scrape", "x")
        runner, _ = self._runner(["test.scrape"], tabs=1, max_tasks=1)
        asyncio.run(asyncio.wait_for(runner.run(), 5))
        self.assertEqual(CALLS, [("scrape", "x", CALLS[0][2])])
        self.assertEqual(self.store.counts()["test.source"], {"queued": 1})

    def test_handler_crash_retries_and_replaces_the_tab(self):
        self.store.enqueue("test.scrape", "boom")
        runner, browser = self._runner(["test.scrape"], tabs=1, max_tasks=1)
        asyncio.run(asyncio.wait_for(runner.run(), 5))
        row = self.store.connection.execute("SELECT state, last_error FROM tasks").fetchone()
        self.assertEqual(row["state"], "queued")
        self.assertIn("tab crashed", row["last_error"])
        self.assertGreaterEqual(browser.counter, 2)

    def test_auth_wall_pauses_everything(self):
        self.store.enqueue("test.scrape", "wall", priority=5)
        self.store.enqueue("test.scrape", "after")
        runner, _ = self._runner(["test.scrape"], tabs=1, max_tasks=1)
        asyncio.run(asyncio.wait_for(runner.run(), 5))
        self.assertEqual(self.store.auth_blocked(), "login wall")
        self.assertEqual(self.store.counts()["test.scrape"], {"auth_blocked": 1, "queued": 1})

    def test_vanished_tab_is_recreated(self):
        async def scenario():
            session, browser = fake_session()
            await session.start()
            pool = TabPool(session, 1)
            await pool.start()
            browser.pages.clear()  # someone closed our tab
            page = await pool.acquire()
            self.assertIn(page, browser.pages)
            pool.release(page)
            self.assertIs(await pool.acquire(), page)

        asyncio.run(scenario())


class HandlerHelpersTest(unittest.TestCase):
    def test_username_normalisation(self):
        self.assertEqual(username_of("@Some.Creator"), "some.creator")
        self.assertEqual(username_of("https://www.instagram.com/some_creator/"), "some_creator")
        self.assertIsNone(username_of("https://www.instagram.com/p/abc/"))
        self.assertIsNone(username_of("explore"))

    def test_mentions_dedupe_and_exclude_self(self):
        names = mentioned_usernames(["hi @Alice and @bob", ["@alice", "@me"], None], exclude=["me"])
        self.assertEqual(names, ["alice", "bob"])

    def test_source_list_reads_mixed_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "seeds.txt")
            with open(path, "w") as out:
                out.write("# seeds\n@alice\nhttps://www.instagram.com/bob/\n")
                out.write(json.dumps({"username": "carol", "niche": "food"}) + "\nalice\n")
            spec = handlers.sourcing.source_list
            ctx = TaskContext(page=None, browser=None, database="", tasks=None)
            result = asyncio.run(spec(ctx, {"key": path, "payload": {"path": path}}))
        self.assertEqual([item.key for item in result.follow_ups], ["alice", "bob", "carol"])
        self.assertEqual(result.follow_ups[2].payload["niche"], "food")

    def test_source_similar_uses_search_pk_and_retries_on_429(self):
        class ScriptedPage(FakePage):
            def __init__(self, responses):
                super().__init__("T1")
                self.url = "https://www.instagram.com/"
                self.responses = responses

            async def evaluate(self, script, *args):
                for marker, response in self.responses.items():
                    if marker in script:
                        return json.dumps(response)
                return json.dumps({"status": 404, "url": "", "data": None, "text": ""})

        search = {"status": 200, "url": "x", "data": {"users": [{"user": {"username": "seed", "pk": "42"}}]}}
        chaining = {"status": 200, "url": "x", "data": {"users": [{"username": "Peer.One"}, {"username": "seed"}]}}
        ctx = TaskContext(page=ScriptedPage({"topsearch": search, "chaining/?target_id=42": chaining}),
                          browser=None, database="", tasks=None)
        task = {"key": "seed", "payload": {"username": "seed"}}
        result = asyncio.run(handlers.sourcing.source_similar(ctx, task))
        self.assertEqual([item.key for item in result.follow_ups], ["peer.one"])

        throttled = {"status": 429, "url": "x", "data": None, "text": "<html>"}
        ctx.page = ScriptedPage({"topsearch": search, "chaining": throttled})
        from microindia_scraper.runtime.pacing import Throttled

        with self.assertRaises(Throttled):  # the runner turns this into a retry after the shared cooldown
            asyncio.run(handlers.sourcing.source_similar(ctx, task))

    def test_handlers_registered(self):
        self.assertEqual(
            handlers_for(["source.*", "scrape.*"]),
            ["scrape.profile", "source.list", "source.search", "source.similar"],
        )


if __name__ == "__main__":
    unittest.main()


class PriorityFloorTest(unittest.TestCase):
    def test_floor_holds_low_priority_work_but_lets_urgent_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(os.path.join(tmp, "t.sqlite3"))
            store.enqueue("source.similar", "deep", priority=2)
            store.enqueue("source.similar", "focus", priority=10)
            floors = {"source.similar": 9}
            self.assertEqual(store.lease("r", ["source.similar"], priority_floor=floors)["key"], "focus")
            self.assertIsNone(store.lease("r", ["source.similar"], priority_floor=floors))
            self.assertEqual(store.lease("r", ["source.similar"])["key"], "deep")
            store.close()


class TabRecycleTest(unittest.TestCase):
    def test_tab_is_replaced_after_n_uses(self):
        async def scenario():
            session, browser = fake_session()
            await session.start()
            pool = TabPool(session, 1, recycle_after=3)
            await pool.start()
            first = await pool.acquire()
            for _ in range(2):
                await pool.finish(first)
                self.assertIs(await pool.acquire(), first)
            await pool.finish(first)  # third use -> recycled
            fresh = await pool.acquire()
            self.assertIsNot(fresh, first)
            self.assertNotIn(first, browser.pages)

        asyncio.run(scenario())


class PacerTest(unittest.TestCase):
    def test_429_doubles_delay_and_cools_down_then_clean_time_speeds_up(self):
        from microindia_scraper.runtime.pacing import CLEAN_WINDOW, COOLDOWN, MIN_DELAY, START_DELAY, Pacer

        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite3")
            TaskStore(db).close()  # creates runtime_flags / usage
            pacer = Pacer(db)
            now = 1000.0
            state = pacer.record(429, now=now)
            self.assertEqual(state["delay"], START_DELAY * 2)
            self.assertEqual(state["cooldown_until"], now + COOLDOWN)
            state = pacer.record(200, now=now + CLEAN_WINDOW + 1)
            self.assertLess(state["delay"], START_DELAY * 2)
            for i in range(60):
                state = pacer.record(200, now=now + (i + 2) * (CLEAN_WINDOW + 1))
            self.assertEqual(state["delay"], MIN_DELAY)
            counts = dict(sqlite3.connect(db).execute("SELECT name, SUM(count) FROM usage GROUP BY name").fetchall())
            self.assertEqual(counts["ig_429"], 1)


class PacerRobustnessTest(unittest.TestCase):
    def test_malformed_state_never_breaks_requests(self):
        from microindia_scraper.runtime.pacing import START_DELAY, Pacer

        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "t.sqlite3")
            TaskStore(db).close()
            connection = sqlite3.connect(db)
            connection.execute("INSERT INTO runtime_flags(name, value, updated_at) VALUES ('pace', ?, 0)",
                               ('{"delay": "2", "cooldown_until": 0, "clean_since": "1791209000", "last_429": null}',))
            connection.commit()
            state = Pacer(db).record(200, now=1791209100.0)  # used to raise TypeError
            self.assertEqual(state["delay"], 2.0)
            connection.execute("UPDATE runtime_flags SET value='garbage' WHERE name='pace'")
            connection.commit()
            self.assertEqual(Pacer(db).state()["delay"], START_DELAY)


@handler("test.offline")
async def _offline_handler(ctx, task):
    raise RuntimeError("Page.goto: net::ERR_INTERNET_DISCONNECTED at https://www.instagram.com/x/")


@handler("test.long", needs_page=False, timeout_seconds=3600)
async def _long_handler(ctx, task):
    row = ctx.tasks.connection.execute("SELECT lease_until FROM tasks WHERE task_id=?", (task["task_id"],)).fetchone()
    CALLS.append(("long", task["key"], row["lease_until"]))
    return Done()


class ReliabilityTest(unittest.TestCase):
    """Network loss pauses instead of failing; stuck slots and stuck browser calls end the runner."""

    def setUp(self):
        CALLS.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "t.sqlite3")
        self.store = TaskStore(self.db, max_attempts=3)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _runner(self, kinds, *, probe=None, **extra):
        session, browser = fake_session()

        async def unreachable():
            return False

        runner = Runner(tasks=self.store, session=session, kinds=kinds, tabs=1, database=self.db,
                        runner_id="rel-runner", poll_seconds=0.01, housekeeping_seconds=0.01,
                        network_probe=probe or unreachable, **extra)
        return runner, browser

    def test_offline_gives_the_attempt_back_and_pauses_everyone(self):
        self.store.enqueue("test.offline", "a")
        runner, _ = self._runner(["test.offline"], max_tasks=1)
        asyncio.run(asyncio.wait_for(runner.run(), 5))
        row = self.store.connection.execute("SELECT state, attempts, run_at FROM tasks").fetchone()
        self.assertEqual((row["state"], row["attempts"]), ("queued", 0))
        self.assertIn("ERR_INTERNET_DISCONNECTED", self.store.network_down())

    def test_offline_looking_error_with_working_network_is_a_normal_retry(self):
        self.store.enqueue("test.offline", "a")

        async def reachable():
            return True

        runner, _ = self._runner(["test.offline"], probe=reachable, max_tasks=1)
        asyncio.run(asyncio.wait_for(runner.run(), 5))
        row = self.store.connection.execute("SELECT state, attempts FROM tasks").fetchone()
        self.assertEqual((row["state"], row["attempts"]), ("queued", 1))
        self.assertIsNone(self.store.network_down())

    def test_offline_never_exhausts_attempts(self):
        task_id = self.store.enqueue("test.offline", "a")
        for _ in range(6):  # more failures than max_attempts
            self.store.connection.execute("UPDATE tasks SET run_at=0 WHERE kind='test.offline'")
            task = self.store.lease("r", ["test.offline"])
            from microindia_scraper.runtime import Offline
            self.store.apply(task, "r", Offline("net::ERR_INTERNET_DISCONNECTED"))
        self.assertEqual(self.store.connection.execute("SELECT state FROM tasks").fetchone()["state"], "queued")

    def test_network_back_clears_pause_and_makes_parked_work_due(self):
        self.store.enqueue("test.offline", "a")
        runner, _ = self._runner(["test.offline"], max_tasks=1)
        asyncio.run(asyncio.wait_for(runner.run(), 5))
        self.assertTrue(self.store.network_down())

        async def reachable():
            return True

        runner2, _ = self._runner(["test.scrape"], probe=reachable)

        async def one_housekeeping_pass():
            task = asyncio.create_task(runner2._housekeeping())
            await asyncio.sleep(0.05)
            runner2.stop()
            await task

        asyncio.run(one_housekeeping_pass())
        self.assertIsNone(self.store.network_down())
        row = self.store.connection.execute("SELECT run_at FROM tasks").fetchone()
        self.assertLessEqual(row["run_at"], time.time())

    def test_runner_with_expired_leases_stops_itself(self):
        self.store.enqueue("test.long", "stuck")
        self.store.lease("rel-runner", ["test.long"], lease_seconds=-1000)  # held long past expiry
        runner, _ = self._runner(["test.long"])
        asyncio.run(asyncio.wait_for(runner._housekeeping(), 5))
        self.assertIn("held past their lease", repr(runner._fatal))

    def test_hanging_tab_operation_stops_the_runner(self):
        self.store.enqueue("test.scrape", "boom")  # crashes -> tab replace, which we make hang
        runner, _ = self._runner(["test.scrape"], max_tasks=1, tab_op_timeout=0.05)

        async def hang(self, tab):
            await asyncio.sleep(3600)

        saved, TabPool.replace = TabPool.replace, hang
        try:
            with self.assertRaises(Exception) as caught:
                asyncio.run(asyncio.wait_for(runner.run(), 5))
        finally:
            TabPool.replace = saved
        self.assertIn("could not replace tab", repr(caught.exception))

    def test_long_handler_extends_its_lease(self):
        self.store.enqueue("test.long", "x")
        runner, _ = self._runner(["test.long"], max_tasks=1)
        started = time.time()
        asyncio.run(asyncio.wait_for(runner.run(), 5))
        self.assertGreater(CALLS[0][2], started + 3600)

    def test_due_count_honours_priority_floor(self):
        self.store.enqueue("test.scrape", "low", priority=1)
        self.store.enqueue("test.scrape", "high", priority=9)
        self.assertEqual(self.store.due_count(["test.scrape"]), 2)
        self.assertEqual(self.store.due_count(["test.scrape"], priority_floor={"test.scrape": 5}), 1)

    def test_requeue_failed_by_error(self):
        for key, error in (("a", "gave up: net::ERR_INTERNET_DISCONNECTED"), ("b", "gave up: parse error")):
            self.store.enqueue("test.scrape", key)
            self.store.connection.execute("UPDATE tasks SET state='failed', attempts=3, last_error=? WHERE key=?",
                                          (error, key))
        self.assertEqual(self.store.requeue_failed(error_like="%ERR_INTERNET_DISCONNECTED%"), 1)
        states = dict(self.store.connection.execute("SELECT key, state FROM tasks").fetchall())
        self.assertEqual(states, {"a": "queued", "b": "failed"})


class LeaseOwnershipTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "t.sqlite3")
        self.store = TaskStore(self.db, max_attempts=3)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_runner_never_releases_its_own_stuck_task_to_itself(self):
        self.store.enqueue("test.scrape", "stuck")
        self.store.lease("r1", ["test.scrape"], lease_seconds=-10)  # slot 1 holds it past expiry
        self.assertIsNone(self.store.lease("r1", ["test.scrape"]))  # slot 2 of the same runner
        self.assertEqual(self.store.expired_leases("r1"), 1)  # so the stall stays visible
        self.assertEqual(self.store.due_count(["test.scrape"], runner_id="r1"), 0)
        self.assertIsNotNone(self.store.lease("r2", ["test.scrape"]))  # another runner may take over

    def test_normal_stop_refunds_the_attempt(self):
        self.store.enqueue("test.scrape", "a")
        self.store.lease("r1", ["test.scrape"])
        self.assertEqual(self.store.refund_leases("r1"), 1)
        row = self.store.connection.execute("SELECT state, attempts, leased_by FROM tasks").fetchone()
        self.assertEqual((row["state"], row["attempts"], row["leased_by"]), ("queued", 0, None))

    def test_hard_exit_refunds_on_clean_stop_but_not_after_a_crash(self):
        from unittest import mock

        for fatal, expected_attempts, expected_code in ((None, 0, 0), (RuntimeError("boom"), 1, 1)):
            self.store.connection.execute("DELETE FROM tasks")
            self.store.enqueue("test.scrape", "a")
            self.store.lease("rx", ["test.scrape"])
            session, _ = fake_session()
            runner = Runner(tasks=self.store, session=session, kinds=["test.scrape"], tabs=1, database=self.db,
                            runner_id="rx", hard_exit_seconds=1)
            runner._fatal = fatal
            with mock.patch("microindia_scraper.runtime.runner.os._exit") as fake_exit:
                runner._hard_exit()
            fake_exit.assert_called_once_with(expected_code)
            attempts = self.store.connection.execute("SELECT attempts FROM tasks").fetchone()["attempts"]
            self.assertEqual(attempts, expected_attempts)

    def test_browser_that_never_starts_ends_the_runner(self):
        session, _ = fake_session()

        async def never():
            await asyncio.sleep(3600)

        session.start = never
        runner = Runner(tasks=self.store, session=session, kinds=["test.scrape"], tabs=1, database=self.db,
                        runner_id="rs", start_timeout=0.05)
        with self.assertRaises(Exception) as caught:
            asyncio.run(asyncio.wait_for(runner.run(), 5))
        self.assertIn("did not start", repr(caught.exception))
        status = self.store.connection.execute("SELECT status FROM runners WHERE runner_id='rs'").fetchone()["status"]
        self.assertEqual(status, "starting")  # visible to the watchdog's stale-heartbeat check
