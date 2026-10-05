import os
import sqlite3
import tempfile
import threading
import time
import unittest

from microindia_scraper.backup import backup_once, daily_backups


class BackupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "live.sqlite3")
        connection = sqlite3.connect(self.db)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE t (x)")
        connection.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(500)])
        connection.commit()
        self.live = connection  # stays open, like the running workers
        self.dir = os.path.join(self.tmp.name, "backups")

    def tearDown(self):
        self.live.close()
        self.tmp.cleanup()

    def test_backup_copies_live_database_and_verifies_it(self):
        path = backup_once(self.db, self.dir, day="2026-10-06")
        copy = sqlite3.connect(path)
        self.assertEqual(copy.execute("SELECT COUNT(*) FROM t").fetchone()[0], 500)
        copy.close()
        self.assertFalse(os.path.exists(path + ".partial"))

    def test_backup_finishes_while_a_writer_keeps_committing(self):
        stop = threading.Event()

        def writer():
            connection = sqlite3.connect(self.db, timeout=10)
            while not stop.is_set():
                connection.execute("INSERT INTO t VALUES (1)")
                connection.commit()
                time.sleep(0.005)
            connection.close()

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            started = time.time()
            path = backup_once(self.db, self.dir, day="2026-10-07")
            self.assertLess(time.time() - started, 10)
        finally:
            stop.set()
            thread.join()
        copy = sqlite3.connect(path)
        self.assertGreaterEqual(copy.execute("SELECT COUNT(*) FROM t").fetchone()[0], 500)
        copy.close()

    def test_keeps_only_the_newest_daily_backups(self):
        os.makedirs(self.dir)
        manual = os.path.join(self.dir, "microindia-2026-10-05-pre-runtime.sqlite3")
        open(manual, "w").close()
        for day in range(1, 10):
            backup_once(self.db, self.dir, day=f"2026-10-0{day}", keep=3)
        names = [os.path.basename(p) for p in daily_backups(self.dir)]
        self.assertEqual(names, ["microindia-2026-10-07.sqlite3", "microindia-2026-10-08.sqlite3",
                                 "microindia-2026-10-09.sqlite3"])
        self.assertTrue(os.path.exists(manual))  # one-off backups are never pruned


if __name__ == "__main__":
    unittest.main()
