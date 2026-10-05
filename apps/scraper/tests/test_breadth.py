import os
import tempfile
import unittest

from microindia_scraper.handlers.sourcing import BREADTH_TARGET, breadth_priority, niche_coverage, seed_searches
from microindia_scraper.niches import NICHES, primary_niche, queries_for
from microindia_scraper.runtime import Done, TaskStore


class NicheTaxonomyTest(unittest.TestCase):
    def test_classifies_a_wide_range_of_niches(self):
        cases = {"BGMI streamer | esports": "gaming", "Dog mom #dogsofinstagram": "pets", "UPSC prep notes": "education",
                 "Royal Enfield rides #motovlog": "auto", "Kathak dancer": "dance", "Mutual funds explained": "finance"}
        for text, niche in cases.items():
            self.assertEqual(primary_niche(text), niche, text)
        self.assertGreaterEqual(len(NICHES), 30)
        self.assertTrue(all(queries_for(n) for n in NICHES))


class BreadthFirstSeedingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tasks = TaskStore(os.path.join(self.tmp.name, "t.sqlite3"))

    def tearDown(self):
        self.tasks.close()
        self.tmp.cleanup()

    def _capture(self, key, niche):
        self.tasks.enqueue("scrape.profile", key)
        task = self.tasks.lease("r", ["scrape.profile"])
        self.tasks.apply(task, "r", Done({"eligible": True, "category": niche}))

    def test_every_niche_gets_searches_and_covered_niches_go_deep(self):
        for i in range(BREADTH_TARGET):
            self._capture(f"food{i}", "food")
        seed_searches(self.tasks)
        rows = self.tasks.connection.execute(
            "SELECT json_extract(payload,'$.niche') AS niche, MAX(priority) AS p FROM tasks WHERE kind='source.search' GROUP BY niche"
        ).fetchall()
        by_niche = {row["niche"]: row["p"] for row in rows}
        self.assertEqual(niche_coverage(self.tasks)["food"], BREADTH_TARGET)
        self.assertGreaterEqual(len(by_niche), len(NICHES) - 1)  # all under-covered niches seeded
        self.assertGreater(by_niche["gaming"], by_niche.get("food", 0))  # breadth before depth

    def test_priority_drops_once_a_niche_has_enough(self):
        self.assertGreater(breadth_priority("pets", {"pets": 1}, wide=8, deep=2), breadth_priority("pets", {"pets": BREADTH_TARGET}, wide=8, deep=2))


if __name__ == "__main__":
    unittest.main()


class BreadthBacklogCapTest(BreadthFirstSeedingTest):
    def test_no_new_searches_while_breadth_backlog_is_full(self):
        from microindia_scraper.handlers.sourcing import BREADTH_BACKLOG_CAP

        for i in range(BREADTH_BACKLOG_CAP + 1):
            self.tasks.enqueue("scrape.profile", f"p{i}", priority=9)
        self.assertEqual(seed_searches(self.tasks), 0)
        self.assertEqual(self.tasks.pending(["source.search"]), 0)


class PreQueueFilterTest(unittest.TestCase):
    def test_skips_private_ai_labelled_and_photoless_accounts(self):
        from microindia_scraper.handlers.sourcing import worth_scraping

        self.assertFalse(worth_scraping({"username": "a", "is_private": True}))
        self.assertFalse(worth_scraping({"username": "b", "aigm_account_label_info": {"label": "AI"}}))
        self.assertFalse(worth_scraping({"username": "c", "has_anonymous_profile_picture": True}))
        self.assertFalse(worth_scraping({"username": "indian.ai.creator"}))
        self.assertTrue(worth_scraping({"username": "_kirubaharan_", "full_name": "Chennai | Actor"}))


class FocusRotationTest(BreadthFirstSeedingTest):
    def test_rotates_when_time_is_up_or_focus_is_barren(self):
        import random
        from microindia_scraper.handlers.sourcing import rotate_focus

        first = rotate_focus(self.tasks, now=1000.0, rng=random.Random(1))
        self.assertEqual(rotate_focus(self.tasks, now=1000.0 + 600, rng=random.Random(2))["started_at"], first["started_at"])
        # Its searches finished and found nobody: now it is barren and moves on.
        self.tasks.connection.execute("UPDATE tasks SET state='done' WHERE kind='source.search'")
        self.tasks.connection.commit()
        barren = rotate_focus(self.tasks, now=1000.0 + 16 * 60, rng=random.Random(3))
        self.assertNotEqual(barren["started_at"], first["started_at"])  # nothing found in 15 min -> moved on


class FocusSeedsSimilarTest(BreadthFirstSeedingTest):
    def test_focus_expands_similar_accounts_of_known_creators_in_its_niche(self):
        import random
        from microindia_scraper.handlers.sourcing import FOCUS_PRIORITY, rotate_focus

        focus = rotate_focus(self.tasks, now=1000.0, rng=random.Random(4))
        for i in range(3):
            self.tasks.enqueue("scrape.profile", f"seed{i}")
            task = self.tasks.lease("r", ["scrape.profile"])
            self.tasks.apply(task, "r", Done({"eligible": True, "category": focus["niche"], "followers": focus["min_followers"] + 10}))
        rotate_focus(self.tasks, now=1000.0 + 60, rng=random.Random(5))
        rows = self.tasks.connection.execute("SELECT key, priority FROM tasks WHERE kind='source.similar'").fetchall()
        self.assertEqual(sorted(r["key"] for r in rows), ["seed0", "seed1", "seed2"])
        self.assertTrue(all(r["priority"] == FOCUS_PRIORITY for r in rows))


class FocusHandoverTest(BreadthFirstSeedingTest):
    def test_new_focus_goes_first_and_queued_work_is_not_barren(self):
        import random
        from microindia_scraper.handlers.sourcing import FOCUS_PRIORITY, rotate_focus

        first = rotate_focus(self.tasks, now=1000.0, rng=random.Random(1))
        self.tasks.enqueue("scrape.profile", "lead-of-first", priority=FOCUS_PRIORITY)
        # 16 minutes, nothing captured, but a lead is still queued: not barren, keep the focus.
        same = rotate_focus(self.tasks, now=1000.0 + 16 * 60, rng=random.Random(2))
        self.assertEqual(same["started_at"], first["started_at"])
        # Time up: rotate, and the old lead drops below the new focus.
        rotate_focus(self.tasks, now=1000.0 + 31 * 60, rng=random.Random(3))
        row = self.tasks.connection.execute("SELECT priority FROM tasks WHERE key='lead-of-first'").fetchone()
        self.assertEqual(row["priority"], 6)


class NicheInheritanceTest(unittest.TestCase):
    def test_own_words_win_then_hint_then_inherited_niche(self):
        from microindia_scraper.handlers.scraping import _niche_from

        self.assertEqual(_niche_from({"bio_text": "Kathak dancer"}, [], {"niche": "pets"}), "dance")
        self.assertEqual(_niche_from({"bio_text": "hello"}, [], {"niche": "pets", "hint": "Home Chef"}), "food")
        self.assertEqual(_niche_from({"bio_text": "hello"}, [], {"niche": "pets"}), "pets")
        self.assertIsNone(_niche_from({"bio_text": "hello"}, [], {"niche": "delhi food blogger"}))


class FocusSeedBudgetTest(BreadthFirstSeedingTest):
    def test_focus_seeds_at_most_its_budget(self):
        import random
        from microindia_scraper.handlers.sourcing import FOCUS_SEEDS, rotate_focus

        focus = rotate_focus(self.tasks, now=1000.0, rng=random.Random(4))
        for i in range(FOCUS_SEEDS + 6):
            self.tasks.enqueue("scrape.profile", f"s{i}")
            task = self.tasks.lease("r", ["scrape.profile"])
            self.tasks.apply(task, "r", Done({"eligible": True, "category": focus["niche"], "followers": focus["min_followers"] + 1}))
        for step in range(6):  # expansions finish quickly; the focus must not keep refilling forever
            rotate_focus(self.tasks, now=1000.0 + 30 * (step + 1), rng=random.Random(5))
            self.tasks.connection.execute("UPDATE tasks SET state='done' WHERE kind='source.similar'")
            self.tasks.connection.commit()
        total = self.tasks.connection.execute("SELECT COUNT(*) FROM tasks WHERE kind='source.similar'").fetchone()[0]
        self.assertEqual(total, FOCUS_SEEDS)
