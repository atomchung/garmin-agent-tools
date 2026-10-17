"""Anonymous parent-browser reports: no real browser, health data or login."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from garmin_agent_tools.browser import read_protocol as r, publisher, write_protocol as w


class PortableBrowser(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="anonymous-parent-browser-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "private"
        self.root.mkdir(mode=0o700)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"GARMIN_AGENT_BROWSER_HOME": str(self.root)}).start()
        patch.object(r, "PRIVATE", self.root / "read").start()
        patch.object(r, "RUNS", self.root / "read/runs").start()
        self.run = r.create_run()
        self.request, self.directory = r.create_request(self.run, 12345)
        observed = r.utc_now().isoformat()
        self.report = {"request_id": self.request.request_id, "workout_id": 12345,
            "operation": "read_workout", "observation_method": "parent_browser_tool",
            "observed_url": "https://connect.garmin.com/app/workout/12345",
            "refreshed_at": observed, "observed_at": observed,
            "name": "ANONYMOUS PARENT REPORT", "sport": None, "steps": None}
        self.file = self.root / "observation.json"

    def record(self, report=None):
        if self.file.exists():
            self.file.unlink()
        r.atomic_json(self.file, self.report if report is None else report)
        publisher.record_parent_read(self.run, self.request.request_id, self.file)

    def test_selected_report_preserves_provenance_unknowns_and_original(self):
        self.record()
        result = r.read_private_json(self.directory / "response.json")
        self.assertEqual(result["observation_method"], "parent_browser_tool")
        self.assertEqual(result["api_authentication"], "unknown")
        self.assertIsNone(result["sport"])
        stored = r.read_private_json(self.directory / "parent-observation.json")
        self.assertEqual(stored["observed_url"], self.report["observed_url"])
        self.assertEqual(stored["refreshed_at"], self.report["refreshed_at"])
        self.assertEqual(r.validate_response(result, self.request).name, self.report["name"])

    def test_wrong_binding_url_old_refresh_future_observation_or_method_blocks_publication(self):
        old = (r.timestamp(self.request.created_at) - timedelta(seconds=1)).isoformat()
        future = (r.utc_now() + timedelta(hours=1)).isoformat()
        cases = [{"request_id": "0"*32}, {"workout_id": 54321},
            {"observed_url": "https://example.invalid/app/workout/12345"},
            {"observed_url": "https://connect.garmin.com/app/workout/54321"},
            {"refreshed_at": old}, {"observed_at": old}, {"observed_at": future},
            {"observation_method": "cua_repl"}, {"api_authentication": "verified"},
            {"approve": True}, {"name": None}]
        for fields in cases:
            with self.subTest(fields=fields):
                report = {**self.report, **fields}
                with self.assertRaises(Exception):
                    self.record(report)
                self.assertFalse((self.directory / "response.json").exists())
                self.assertFalse((self.directory / "parent-observation.json").exists())

    def test_expired_request_or_unprotected_file_blocks_publication(self):
        r.atomic_json(self.file, self.report)
        self.file.chmod(0o644)
        with self.assertRaises(r.ProtocolError):
            publisher.record_parent_read(self.run, self.request.request_id, self.file)
        self.file.chmod(0o600)
        with patch.object(r, "utc_now", return_value=r.timestamp(self.request.expires_at)):
            with self.assertRaises(r.ProtocolError):
                publisher.record_parent_read(self.run, self.request.request_id, self.file)
        self.assertFalse((self.directory / "response.json").exists())

    def test_second_submission_cannot_replace_existing_evidence(self):
        self.record()
        original = (self.directory / "response.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self.record({**self.report, "name": "REPLACEMENT"})
        self.assertEqual((self.directory / "response.json").read_bytes(), original)

    def test_generic_name_and_old_dot_proposal_remain_valid_without_approval(self):
        state = self.root / "write"
        for name in ["AGENT MCP BROWSER TEST ANONYMOUS", "DOT MCP BROWSER TEST ANONYMOUS"]:
            proposal = w.preview_test(state, {"name": name, "date": "2026-10-18"})
            args = {key: proposal[key] for key in ("preview_id", "proposal_sha256")}
            self.assertEqual(w.load_proposal(state, w.ApplyInput(**args))["name"], name)
            self.assertEqual(w.apply_test(state, args)["status"], "awaiting_owner_confirmation")
        self.assertEqual(list((state / "queue").iterdir()), [])

    def test_training_reference_is_readable_without_creating_athlete_state(self):
        empty_state = self.root / "not-created"
        proc = subprocess.run([sys.executable, "-m", "garmin_agent_tools.browser", "training-guidance"],
            env={**os.environ, "GARMIN_AGENT_BROWSER_HOME": str(empty_state)},
            text=True, capture_output=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertFalse(data["athlete_context_loaded"])
        self.assertFalse(data["current_plan_loaded"])
        self.assertIn("source_revision", data["source"])
        self.assertTrue(data["text"])
        self.assertFalse(empty_state.exists())


if __name__ == "__main__":
    unittest.main()
