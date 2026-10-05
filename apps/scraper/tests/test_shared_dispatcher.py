import asyncio
import unittest
from unittest.mock import patch

from microindia_scraper.shared_dispatcher import dispatch_batch


class DispatcherTests(unittest.TestCase):
    def test_dispatch_batch_respects_requested_capture_bound(self):
        active = 0
        maximum = 0

        async def fake_dispatch(*args, **kwargs):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0)
            active -= 1
            return True

        with patch("microindia_scraper.shared_dispatcher.dispatch_once", side_effect=fake_dispatch) as dispatch:
            result = asyncio.run(dispatch_batch(None, None, "owner", "db", 3, discovery_target_id="discovery"))

        self.assertEqual(result, [True, True, True])
        self.assertEqual(maximum, 3)
        self.assertEqual(dispatch.call_count, 3)

    def test_empty_dispatch_batch_does_not_schedule_capture(self):
        with patch("microindia_scraper.shared_dispatcher.dispatch_once") as dispatch:
            self.assertEqual(asyncio.run(dispatch_batch(None, None, "owner", "db", 0)), [])
        dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
