"""NEW focused regressions only; all proposals/approvals/observations are anonymous TEMP fixtures."""
import copy
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from garmin_agent_tools.browser import write_protocol as p


class NormalFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="anonymous-normal-flow-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "state"
        p.private_dir(self.home)
        self.path = p.runtime(self.home / "write")
        self.preview = p.preview_test(self.path, {"name": "DOT MCP BROWSER TEST ANONYMOUS", "date": "2026-10-18"})
        self.args = {key: self.preview[key] for key in ("preview_id", "proposal_sha256")}

    def fake_approval(self):
        current = p.now()
        p.save_once(self.path / "approvals" / (self.args["preview_id"] + ".json"), p.canonical({
            "schema_version": "owner-confirmation-v1", **self.args,
            "name": self.preview["proposal"]["name"], "date": self.preview["proposal"]["date"],
            "owner_reply_reference": "SYNTHETIC-ANONYMOUS-TEMP-ONLY-NOT-OWNER-APPROVAL",
            "owner_reply_at": p.stamp(current), "recorded_at": p.stamp(current),
            "expires_at": p.stamp(current + timedelta(minutes=10))}))

    def claim(self):
        self.fake_approval()
        self.assertEqual(p.apply_test(self.path, self.args)["status"], "pending_parent_cua")
        p.claim_execution(self.path, self.args)

    def selected(self):
        return {"schema_version": "selected-browser-observation-v1", **self.args,
                "source_type": "browser_ui", "agent_mediated": True, "observed_at": p.stamp(),
                "workout": {"workout_id": 123456, "name": self.preview["proposal"]["name"],
                            "sport": "running", "steps": copy.deepcopy(self.preview["proposal"]["steps"])},
                "calendar": {"workout_id": 123456, "date": self.preview["proposal"]["date"]}}

    def protected_snapshot(self):
        return {str(file.relative_to(self.path)): file.read_bytes()
                for part in ("proposals", "approvals", "claims", "queue", "execution", "outcomes", "journal")
                for file in (self.path / part).iterdir()}

    def assert_snapshot_preserved(self, before):
        for name, raw in before.items():
            self.assertEqual((self.path / name).read_bytes(), raw, name)



    def test_exact_match_only_bounded_agent_report_no_api_or_watch_claim(self):
        self.claim()
        result = p.record_browser_observation(self.path, self.args, self.selected())
        readback = p.apply_test(self.path, self.args)
        self.assertEqual(result["status"], "browser_observed_scheduled")
        self.assertTrue(readback["browser_readback_match"])
        self.assertFalse(readback["independently_verified"])
        self.assertTrue(readback["agent_mediated"])
        self.assertEqual(readback["source_type"], "browser_ui")
        self.assertEqual(readback["api_authentication"], "unknown")
        self.assertEqual(readback["watch"], "unknown")
        self.assertEqual(readback["verification"], "agent_reported_browser_ui_not_independently_verified")
        self.assertNotIn("external_success", readback)
        self.assertFalse(readback["automatic_retry"])

    def test_date_target_workout_name_sport_steps_and_missing_mismatches_fail_closed(self):
        self.claim()
        variants = []
        for field, value in (("date", "2026-10-19"), ("workout_id", 999)):
            value_obs = self.selected(); value_obs["calendar"][field] = value; variants.append(value_obs)
        for field, value in (("name", "DOT MCP BROWSER TEST OTHER"), ("sport", "cycling"), ("steps", [])):
            value_obs = self.selected(); value_obs["workout"][field] = value; variants.append(value_obs)
        value_obs = self.selected(); value_obs["workout"]["steps"][1]["target"] = "HeartRate"; variants.append(value_obs)
        value_obs = self.selected(); value_obs["workout"]["steps"][1]["seconds"] = 601; variants.append(value_obs)
        for field in ("workout", "calendar"):
            value_obs = self.selected(); value_obs.pop(field); variants.append(value_obs)
        self.assertEqual(p.apply_test(self.path, self.args)["status"], "uncertain")
        for selected in variants:
            with self.subTest(selected=selected):
                result = p.record_browser_observation(self.path, self.args, selected)
                self.assertEqual(result["status"], "uncertain")
                self.assertFalse(p.apply_test(self.path, self.args)["browser_readback_match"])
        self.assertEqual(len(list((self.path / "queue").iterdir())), 1)

    def test_freshness_preview_sha_and_execution_binding(self):
        self.claim()
        execution = p.read_json(self.path / "execution" / (self.args["preview_id"] + ".json"))
        variants = [("preview_id", "0" * 32), ("proposal_sha256", "0" * 64),
                    ("observed_at", execution["at"]),
                    ("observed_at", p.stamp(p.instant(execution["at"]) - timedelta(seconds=1))),
                    ("observed_at", p.stamp(p.now() + timedelta(days=1)))]
        for field, value in variants:
            selected = self.selected(); selected[field] = value
            with self.subTest(field=field, value=value):
                self.assertEqual(p.record_browser_observation(self.path, self.args, selected)["status"], "uncertain")
                self.assertEqual(p.apply_test(self.path, self.args)["status"], "uncertain")
        for timestamp in ("2026-10-17T01:00:00", "not-a-timestamp"):
            selected = self.selected(); selected["observed_at"] = timestamp
            with self.assertRaises((p.ProtocolError, ValueError)):
                p.record_browser_observation(self.path, self.args, selected)

    def test_observation_before_execution_is_blocked(self):
        self.fake_approval()
        p.apply_test(self.path, self.args)
        with self.assertRaises(FileNotFoundError):
            p.record_browser_observation(self.path, self.args, self.selected())
        self.assertEqual(len(list((self.path / "observations").iterdir())), 0)
        self.assertEqual(p.apply_test(self.path, self.args)["status"], "pending_parent_cua")

    def test_partial_reconciliation_appends_evidence_and_never_redispatches(self):
        self.claim()
        p.record_worker_outcome(self.path, self.args, "partial")
        incomplete = self.selected(); incomplete.pop("calendar")
        p.record_browser_observation(self.path, self.args, incomplete)
        before = self.protected_snapshot()
        old_observation = {file: file.read_bytes() for file in (self.path / "observations").iterdir()}
        self.assertEqual(p.apply_test(self.path, self.args)["status"], "uncertain")
        p.record_browser_observation(self.path, self.args, self.selected())
        for _ in range(3):
            result = p.apply_test(self.path, self.args)
            self.assertEqual(result["status"], "browser_observed_scheduled")
            self.assertFalse(result["redispatched"])
            self.assertFalse(result["automatic_retry"])
        self.assert_snapshot_preserved(before)
        for file, raw in old_observation.items():
            self.assertEqual(file.read_bytes(), raw)
        self.assertEqual(len(list((self.path / "observations").iterdir())), 2)
        self.assertEqual(len(list((self.path / "queue").iterdir())), 1)
        self.assertEqual(len(list((self.path / "execution").iterdir())), 1)

    def test_malformed_observation_and_helper_cli_fixed_error(self):
        self.claim()
        for patch in ({"provider_body": "DO-NOT-ECHO"}, {"source_type": "api"}, {"agent_mediated": False}, {"agent_mediated": 1}):
            with self.assertRaises(ValueError):
                p.record_browser_observation(self.path, self.args, {**self.selected(), **patch})
        for value in (0, -1, True):
            selected = self.selected(); selected["workout"]["workout_id"] = value
            with self.assertRaises(ValueError):
                p.record_browser_observation(self.path, self.args, selected)
        source = Path(self.temp.name) / "observation.json"
        p.save_once(source, b'{"provider_body":"DO-NOT-ECHO"}\n')
        command = [sys.executable, "-m", "garmin_agent_tools.browser", "record-observation",
                   "--preview-id", self.args["preview_id"], "--proposal-sha256", self.args["proposal_sha256"],
                   "--observation-file", str(source)]
        result = subprocess.run(command, cwd=Path(__file__).resolve().parent.parent / "src", text=True, capture_output=True, timeout=10,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                                     "GARMIN_AGENT_BROWSER_HOME": str(self.home), "GARMIN_WRITEPREP_RUNTIME": str(self.path)})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, '{"error":"invalid_or_blocked_browser_operation; no automatic retry"}\n')
        self.assertEqual(len(list((self.path / "observations").iterdir())), 0)

    def test_tampered_observation_cannot_claim_match(self):
        self.claim()
        receipt = p.record_browser_observation(self.path, self.args, self.selected())
        file = self.path / "observations" / (self.args["preview_id"] + "-" + receipt["receipt_id"] + ".json")
        # Same-UID mutation is simulated only inside this disposable fixture.
        data = p.read_json(file); data["observation"]["calendar"]["date"] = "2026-10-19"
        file.chmod(0o600); file.write_bytes(p.canonical(data)); file.chmod(0o400)
        self.assertEqual(p.apply_test(self.path, self.args)["status"], "uncertain")
        self.assertEqual(len(list((self.path / "queue").iterdir())), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
