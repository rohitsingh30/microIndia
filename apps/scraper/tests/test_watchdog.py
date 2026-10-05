import unittest

from microindia_scraper.watchdog import Observation, Policy, WatchdogState, decide


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

    def test_stuck_queue_restarts_runners_but_not_when_auth_blocked(self):
        stuck = obs(5000, last_finished_at=3000)
        self.assertEqual({a["worker"] for a in decide(stuck, self.state, self.policy)}, {"sourcer", "scraper"})
        fresh = WatchdogState(started_at=0.0)
        self.assertEqual(decide(obs(5000, last_finished_at=3000, auth_blocked="login"), fresh, self.policy), [])

    def test_empty_queue_is_not_stuck(self):
        self.assertEqual(decide(obs(5000, last_finished_at=3000, queued=0), self.state, self.policy), [])


if __name__ == "__main__":
    unittest.main()
