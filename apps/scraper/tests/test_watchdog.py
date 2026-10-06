import unittest

from microindia_scraper.watchdog import Observation, Policy, RunnerProgress, WatchdogState, decide


def obs(now, **overrides):
    values = dict(now=now, cdp_ok=True, heartbeats={"sourcer": now, "scraper": now},
                  last_finished_at=now, queued=10, auth_blocked=None)
    values.update(overrides)
    return Observation(**values)


class WatchdogDecisionTest(unittest.TestCase):
    def setUp(self):
        self.state = WatchdogState(started_at=0.0)
        self.policy = Policy()

    def test_healthy_system_needs_nothing(self):
        self.assertEqual(decide(obs(1000), self.state, self.policy), [])

    def test_chrome_restarted_after_three_cdp_failures_then_cools_down(self):
        self.assertEqual(decide(obs(1000, cdp_ok=False), self.state, self.policy), [])
        self.assertEqual(decide(obs(1030, cdp_ok=False), self.state, self.policy), [])
        actions = decide(obs(1060, cdp_ok=False), self.state, self.policy)
        self.assertEqual([a["worker"] for a in actions], ["chrome"])
        for t in (1090, 1120, 1150):
            self.assertEqual(decide(obs(t, cdp_ok=False), self.state, self.policy), [])
        # Past the grace period a still-dead Chrome is restarted again.
        self.assertEqual([a["worker"] for a in decide(obs(1250, cdp_ok=False), self.state, self.policy)], ["chrome"])

    def test_cdp_recovery_resets_failure_count(self):
        decide(obs(1000, cdp_ok=False), self.state, self.policy)
        decide(obs(1030, cdp_ok=False), self.state, self.policy)
        decide(obs(1060, cdp_ok=True), self.state, self.policy)
        self.assertEqual(decide(obs(1090, cdp_ok=False), self.state, self.policy), [])

    def test_stale_runner_heartbeat_restarts_that_runner(self):
        actions = decide(obs(2000, heartbeats={"sourcer": 2000, "scraper": 1500}), self.state, self.policy)
        self.assertEqual(actions, [{"worker": "scraper", "reason": "no heartbeat for 500s"}])

    def test_dead_scraper_is_caught_while_sourcer_keeps_finishing(self):
        # 5 Oct: the sourcer kept finishing tasks, which hid a scraper stalled for 83 minutes.
        progress = {"sourcer": RunnerProgress(due=40, last_finished_at=4990, expired_leases=0),
                    "scraper": RunnerProgress(due=27000, last_finished_at=3000, expired_leases=0)}
        actions = decide(obs(5000, last_finished_at=4990, progress=progress), self.state, self.policy)
        self.assertEqual([a["worker"] for a in actions], ["scraper"])

    def test_leases_held_past_expiry_mean_stuck_slots(self):
        progress = {"scraper": RunnerProgress(due=100, last_finished_at=4990, expired_leases=8)}
        actions = decide(obs(5000, progress=progress), self.state, self.policy)
        self.assertEqual(actions, [{"worker": "scraper", "reason": "8 task(s) held past their lease: slots stuck"}])

    def test_held_back_sourcer_is_not_stuck(self):
        # Backpressure: the sourcer reports nothing it may take, so silence is expected.
        progress = {"sourcer": RunnerProgress(due=0, last_finished_at=1000, expired_leases=0)}
        self.assertEqual(decide(obs(5000, progress=progress), self.state, self.policy), [])

    def test_no_restarts_while_signed_out_or_offline(self):
        progress = {"scraper": RunnerProgress(due=100, last_finished_at=1000, expired_leases=3)}
        self.assertEqual(decide(obs(5000, progress=progress, auth_blocked="login"), self.state, self.policy), [])
        fresh = WatchdogState(started_at=0.0)
        self.assertEqual(decide(obs(5000, progress=progress, network_down="ERR_INTERNET_DISCONNECTED"),
                                fresh, self.policy), [])

    def test_restarted_runner_gets_grace_before_next_restart(self):
        progress = {"scraper": RunnerProgress(due=100, last_finished_at=1000, expired_leases=0)}
        self.assertEqual(len(decide(obs(5000, progress=progress), self.state, self.policy)), 1)
        self.assertEqual(decide(obs(5060, progress=progress), self.state, self.policy), [])

    def test_empty_queue_is_not_stuck(self):
        progress = {"scraper": RunnerProgress(due=0, last_finished_at=1000, expired_leases=0)}
        self.assertEqual(decide(obs(5000, progress=progress), self.state, self.policy), [])


if __name__ == "__main__":
    unittest.main()


class ChromeHealthTest(unittest.TestCase):
    def setUp(self):
        self.state = WatchdogState(started_at=0.0)

    def test_pages_not_loading_restarts_chrome_not_the_runners(self):
        progress = {"scraper": RunnerProgress(due=30000, last_finished_at=1000, expired_leases=0)}
        actions = decide(obs(5000, progress=progress, nav_timeouts=95, nav_successes=0), self.state, Policy())
        self.assertEqual([a["worker"] for a in actions], ["chrome"])
        self.assertIn("pages not loading", actions[0]["reason"])

    def test_some_successes_mean_chrome_is_fine(self):
        self.assertEqual(decide(obs(5000, nav_timeouts=20, nav_successes=3), self.state, Policy()), [])

    def test_offline_is_not_chromes_fault(self):
        self.assertEqual(decide(obs(5000, nav_timeouts=50, nav_successes=0, network_down="ERR_INTERNET_DISCONNECTED"),
                                self.state, Policy()), [])

    def test_updated_chrome_is_restarted_once_then_cools_down(self):
        actions = decide(obs(5000, chrome_running="154.0.8037.93", chrome_installed="154.0.8037.98"), self.state, Policy())
        self.assertIn("updated on disk", actions[0]["reason"])
        self.assertEqual(decide(obs(5060, chrome_running="154.0.8037.93", chrome_installed="154.0.8037.98"),
                                self.state, Policy()), [])
        self.assertEqual(decide(obs(9000, chrome_running="154.0.8037.98", chrome_installed="154.0.8037.98"),
                                self.state, Policy()), [])


class AlertsTest(unittest.TestCase):
    def test_alert_after_30_minutes_then_hourly_and_reset_when_fixed(self):
        from microindia_scraper.watchdog import Alerts
        sent = []
        alerts = Alerts()
        send = lambda title, message: sent.append(message)  # noqa: E731
        issue = "runner scraper stalled: 30000 tasks it may take, no progress for 40 min"
        alerts.update([issue], 0, send)
        alerts.update([issue], 1700, send)
        self.assertEqual(sent, [])
        alerts.update([issue], 1800, send)
        alerts.update([issue], 3000, send)
        self.assertEqual(len(sent), 1)
        alerts.update([issue], 5400, send)
        self.assertEqual(len(sent), 2)
        alerts.update([], 5500, send)
        alerts.update([issue], 5600, send)
        self.assertEqual(len(sent), 2)  # fixed and back: a fresh 30-minute clock
