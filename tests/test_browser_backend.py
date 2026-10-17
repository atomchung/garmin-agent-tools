"""Anonymous offline integration: no Garmin browser, account, credentials or writes."""
import asyncio
import copy
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path

from garmin_agent_tools.browser import write_protocol as p


class BrowserBackend(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="anonymous-browser-package-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "state"
        self.env = {**os.environ, "GARMIN_AGENT_BROWSER_HOME": str(self.root),
                    "GARMIN_AGENT_SESSION": "SYNTHETIC-MUST-NOT-BE-READ",
                    "PYTHONDONTWRITEBYTECODE": "1"}

    def command(self, *args):
        return [sys.executable, "-m", "garmin_agent_tools.browser", *args]

    def call(self, *args):
        result = subprocess.run(self.command(*args), env=self.env, text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertNotIn("SYNTHETIC-MUST-NOT-BE-READ", result.stdout)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def preview(self):
        rows = self.call("preview", "--name", "DOT MCP BROWSER TEST ANONYMOUS", "--date", "2026-10-18")
        self.assert_metadata(rows)
        return rows[-1]["value"]["structuredContent"]

    def args(self, preview):
        return {k: preview[k] for k in ("preview_id", "proposal_sha256")}

    def apply(self, preview):
        return self.call("apply-existing", "--preview-id", preview["preview_id"],
                         "--proposal-sha256", preview["proposal_sha256"])

    def assert_metadata(self, rows):
        self.assertEqual(rows[0]["stage"], "public_mcp_context")
        value = rows[0]["value"]
        self.assertIn("outside-MCP", value["initialize_instructions"])
        self.assertTrue(value["get_prompt_matches_initialize_instructions"])
        tools = value["tools"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["read_workout", "preview_test", "apply_test"])
        self.assertTrue(tools[0]["annotations"]["readOnlyHint"])
        self.assertFalse(tools[1]["annotations"]["readOnlyHint"])
        self.assertTrue(tools[2]["annotations"]["openWorldHint"])
        self.assertEqual(value["prompts"]["prompts"][0]["name"], "browser_workflow")

    def fake_approval(self, preview):
        now = p.now()
        p.save_once(self.root / "write/approvals" / (preview["preview_id"] + ".json"), p.canonical({
            "schema_version": "owner-confirmation-v1", **self.args(preview), "browser_account_key": "a" * 64,
            "name": preview["proposal"]["name"], "date": preview["proposal"]["date"],
            "owner_reply_reference": "ANONYMOUS-TEMPORARY-FIXTURE-NOT-A-HUMAN-APPROVAL",
            "owner_reply_at": p.stamp(now), "recorded_at": p.stamp(now),
            "expires_at": p.stamp(now + timedelta(minutes=10))}))

    def selected(self, preview):
        return {"schema_version": "selected-browser-observation-v1", **self.args(preview),
                "source_type": "browser_ui", "agent_mediated": True, "observed_at": p.stamp(),
                "browser_account_key": "a" * 64,
                "workout": {"workout_id": 12345, "name": preview["proposal"]["name"],
                            "sport": "running", "steps": copy.deepcopy(preview["proposal"]["steps"])},
                "calendar": {"workout_id": 12345, "date": preview["proposal"]["date"]}}

    def test_inspect_has_real_merged_catalogue_without_tool_call(self):
        rows = self.call("inspect")
        self.assert_metadata(rows)
        self.assertEqual(rows[-1]["value"], {"tool_invoked": False})
        for name in ("proposals", "approvals", "queue", "execution"):
            self.assertEqual(list((self.root / "write" / name).iterdir()), [])

    def test_unapproved_preview_reused_without_receipt_collision(self):
        preview = self.preview()
        first, second = self.apply(preview), self.apply(preview)
        for rows in (first, second):
            self.assert_metadata(rows)
            result = rows[-1]["value"]["structuredContent"]
            self.assertEqual(result["status"], "awaiting_owner_confirmation")
            self.assertFalse(result["queued"])
        self.assertNotEqual(first[0]["run_id"], second[0]["run_id"])
        self.assertEqual(len(list((self.root / "write/proposals").iterdir())), 1)
        for part in ("approvals", "claims", "journal", "queue", "execution", "observations"):
            self.assertEqual(list((self.root / "write" / part).iterdir()), [])

    def test_existing_operator_write_runtime_is_bound_into_the_server(self):
        preserved = Path(self.temp.name) / "preserved-private-runtime"
        self.env["GARMIN_WRITEPREP_RUNTIME"] = str(preserved)
        preview = self.preview()
        original = (preserved / "proposals" / (preview["preview_id"] + ".json")).read_bytes()
        rows = self.apply(preview)
        self.assertEqual(rows[0]["value"]["write_runtime"], str(preserved))
        self.assertEqual(rows[-1]["value"]["structuredContent"]["status"], "awaiting_owner_confirmation")
        self.assertEqual((preserved / "proposals" / (preview["preview_id"] + ".json")).read_bytes(), original)
        self.assertFalse((self.root / "write").exists())

    def test_synthetic_approved_dispatch_and_mcp_readback_do_not_redispatch(self):
        preview = self.preview()
        self.fake_approval(preview)
        pending = self.apply(preview)[-1]["value"]["structuredContent"]
        self.assertEqual(pending["status"], "pending_parent_cua")
        p.claim_execution(self.root / "write", self.args(preview), browser_account_key="a" * 64)
        p.record_browser_observation(self.root / "write", self.args(preview), self.selected(preview))
        before = {file: file.read_bytes() for part in ("claims", "queue", "execution", "journal")
                  for file in (self.root / "write" / part).iterdir()}
        result = self.apply(preview)[-1]["value"]["structuredContent"]
        self.assertEqual(result["status"], "browser_observed_scheduled")
        self.assertFalse(result["redispatched"])
        self.assertFalse(result["independently_verified"])
        self.assertEqual(result["watch"], "unknown")
        self.assertEqual(result["api_authentication"], "unknown")
        for file, raw in before.items():
            self.assertEqual(file.read_bytes(), raw)
        self.assertEqual(len(list((self.root / "write/queue").iterdir())), 1)

    def test_mismatched_target_or_calendar_never_claims_match(self):
        preview = self.preview()
        self.fake_approval(preview)
        self.apply(preview)
        p.claim_execution(self.root / "write", self.args(preview), browser_account_key="a" * 64)
        first = self.selected(preview)
        first["calendar"]["date"] = "2026-10-19"
        second = self.selected(preview)
        second["workout"]["steps"][1]["target"] = "HeartRate"
        for data in (first, second):
            p.record_browser_observation(self.root / "write", self.args(preview), data)
            value = self.apply(preview)[-1]["value"]["structuredContent"]
            self.assertEqual(value["status"], "uncertain")
            self.assertFalse(value["browser_readback_match"])

    def test_two_sdk_read_processes_each_require_new_observation(self):
        requests = []
        for iteration in range(2):
            output = Path(self.temp.name) / f"synthetic-read-{iteration}.jsonl"
            with output.open("w") as stream:
                process = subprocess.Popen(self.command("read-workout", "--workout-id", "12345"),
                    env=self.env, stdout=stream, stderr=subprocess.PIPE, text=True)
                try:
                    deadline = time.monotonic() + 12
                    meta = None
                    while time.monotonic() < deadline:
                        rows = [json.loads(line) for line in output.read_text().splitlines(keepends=True) if line.endswith("\n")]
                        found = [row for row in rows if row["stage"] == "request_ready"]
                        if found:
                            meta = found[0]["value"]
                            break
                        self.assertIsNone(process.poll(), output.read_text())
                        time.sleep(0.02)
                    self.assertIsNotNone(meta)
                    requests.append(meta)
                    selected = {"observed_at": p.stamp(), "name": "ANONYMOUS OFFLINE WORKOUT",
                                "sport": None, "steps": [{"name": "Synthetic", "duration_seconds": 17, "targets": None}],
                                "calendar_evidence": "unknown", "watch_evidence": "unknown", "api_authentication": "unknown"}
                    p.save_once(Path(meta["request_dir"]) / "selected-observation.json", p.canonical(selected))
                    self.call("publish-read", "--run-dir", meta["run_dir"], "--request-id", meta["request_id"])
                    _, errors = process.communicate(timeout=12)
                    self.assertEqual(process.returncode, 0, errors)
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.communicate(timeout=10)
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assert_metadata(rows)
            value = rows[-1]["value"]["structuredContent"]
            self.assertEqual(value["steps"][0]["duration_seconds"], 17)
            self.assertEqual(value["api_authentication"], "unknown")
            self.assertEqual(value["workout_id"], 12345)
            self.assertEqual(value["request_id"], meta["request_id"])
        self.assertNotEqual(requests[0]["request_id"], requests[1]["request_id"])
        self.assertNotEqual(requests[0]["run_dir"], requests[1]["run_dir"])
        for meta in requests:
            self.assertTrue((Path(meta["request_dir"]) / "response.json").exists())

    def test_actual_mcp_rejects_wrong_types_and_self_approval(self):
        async def probe():
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            params = StdioServerParameters(command=sys.executable,
                args=["-m", "garmin_agent_tools.browser", "serve"],
                cwd=str(Path(__file__).resolve().parent.parent / "src"),
                env={"GARMIN_AGENT_BROWSER_HOME": str(self.root)})
            async with stdio_client(params) as (incoming, outgoing):
                async with ClientSession(incoming, outgoing) as session:
                    await session.initialize()
                    for value in (True, "12345", -1, 0):
                        result = await session.call_tool("read_workout", {"workout_id": value})
                        self.assertTrue(result.isError)
                    result = await session.call_tool("apply_test", {"preview_id": "0"*32,
                        "proposal_sha256": "0"*64, "approve": True})
                    self.assertTrue(result.isError)
        asyncio.run(probe())


if __name__ == "__main__":
    unittest.main()
