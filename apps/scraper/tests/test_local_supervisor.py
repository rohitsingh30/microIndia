import json
import os
import tempfile
import unittest
from pathlib import Path
from microindia_scraper.local_supervisor import load_specs, status


class LocalSupervisorTests(unittest.TestCase):
    def test_load_specs_validates_unique_workers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workers.json"
            path.write_text(json.dumps({"workers": [
                {"name": "one", "command": ["echo", "one"]},
                {"name": "two", "command": ["echo", "two"], "restart": False},
            ]}))
            specs = load_specs(str(path))
            self.assertEqual([spec.name for spec in specs], ["one", "two"])
            self.assertFalse(specs[1].restart)

    def test_load_specs_supports_unlimited_restarts_and_environment_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workers.json"
            path.write_text(json.dumps({
                "workers": [{
                    "name": "continuous",
                    "command": ["$HOME", "worker"],
                    "max_restarts": None,
                }]
            }))
            spec = load_specs(str(path))[0]
            self.assertIsNone(spec.max_restarts)
            self.assertEqual(spec.command, [os.environ["HOME"], "worker"])


    def test_status_handles_missing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            result = status(str(Path(directory) / "missing.json"))
            self.assertIsNone(result["supervisor_pid"])
            self.assertEqual(result["workers"], {})


if __name__ == "__main__":
    unittest.main()