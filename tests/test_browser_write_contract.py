"""Anonymous local fixtures only. Synthetic approvals never touch real runtime."""
import json
import os
import stat
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from garmin_agent_tools.browser import write_protocol as p


class WritePreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="anonymous-writeprep-test-")
        self.addCleanup(self.temporary.cleanup)
        self.path = p.runtime(Path(self.temporary.name) / "runtime")
        self.preview = p.preview_test(self.path, {"name": "DOT MCP BROWSER TEST MOCK", "date": "2026-10-18"})
        self.args = {key: self.preview[key] for key in ("preview_id", "proposal_sha256")}

    def count(self, part):
        return len(list((self.path / part).iterdir()))

    def fake_approval(self, **changes):
        # Explicitly synthetic operator input in isolated temporary fixture only.
        current = p.now()
        data = {"schema_version": "owner-confirmation-v1", **self.args, "browser_account_key": "a" * 64,
                "name": self.preview["proposal"]["name"], "date": self.preview["proposal"]["date"],
                "owner_reply_reference": "ANONYMOUS-MOCK-NOT-REAL-OWNER-REPLY",
                "owner_reply_at": p.stamp(current), "recorded_at": p.stamp(current),
                "expires_at": p.stamp(current + timedelta(seconds=300)), **changes}
        p.save_once(self.path / "approvals" / (self.args["preview_id"] + ".json"), p.canonical(data))

    def test_exact_bytes_fixed_effects_and_permissions(self):
        path = self.path / "proposals" / (self.args["preview_id"] + ".json")
        raw = p.read_bytes(path)
        self.assertEqual(p.digest(raw), self.args["proposal_sha256"])
        self.assertEqual(raw, p.canonical(self.preview["proposal"]))
        self.assertTrue(raw.endswith(b"\n") and not raw.endswith(b"\n\n"))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o700)
        self.assertEqual(self.preview["proposal"]["steps"], [
            {"name": "warmup", "seconds": 300, "target": "NoTarget"},
            {"name": "run", "seconds": 600, "target": "NoTarget"},
            {"name": "cooldown", "seconds": 300, "target": "NoTarget"}])
        self.assertEqual(len(self.preview["proposal"]["effects"]), 2)

    def test_no_approval_hash_is_not_approval_zero_queue(self):
        for _ in range(2):
            result = p.apply_test(self.path, self.args)
            self.assertEqual(result["status"], "awaiting_owner_confirmation")
            self.assertFalse(result["queued"])
        for part in ("approvals", "queue", "claims", "journal"):
            self.assertEqual(self.count(part), 0)

    def test_confirmation_without_account_identity_never_queues(self):
        self.fake_approval(browser_account_key=None)
        self.assertEqual(p.apply_test(self.path, self.args)["status"], "awaiting_owner_confirmation")
        self.assertEqual(self.count("queue"), 0)
        self.assertEqual(self.count("claims"), 0)

    def test_profile_link_fingerprint_is_bounded_and_not_a_login(self):
        key = p.account_key_from_profile_url("https://connect.garmin.com/app/profile/ANONYMOUS")
        self.assertEqual(key, p.account_key_from_profile_url("https://connect.garmin.com:443/app/profile/ANONYMOUS/"))
        self.assertNotEqual(key, p.account_key_from_profile_url("https://connect.garmin.com/app/profile/ANOTHER-ANONYMOUS"))
        for value in ("https://example.invalid/app/profile/ANONYMOUS", "https://connect.garmin.com/app/profile/ANONYMOUS?token=FAKE",
                      "https://user:FAKE@connect.garmin.com/app/profile/ANONYMOUS", "ANONYMOUS", None):
            with self.assertRaises(p.ProtocolError):
                p.account_key_from_profile_url(value)

    def test_no_self_approval_or_extra_fields(self):
        for extra in ({"approve": True}, {"owner_reply_reference": "yes"}, {"approval_sha256": self.args["proposal_sha256"]}):
            with self.assertRaises(ValueError):
                p.apply_test(self.path, {**self.args, **extra})
        self.assertEqual(self.count("queue"), 0)

    def test_explicit_name_date_and_prefix_required(self):
        for args in ({"name": "DOT MCP BROWSER TEST X"}, {"date": "2026-10-18"},
                     {"name": "AGENT TEST X", "date": "2026-10-18"},
                     {"name": "DOT MCP BROWSER TEST X", "date": "2026-02-30"},
                     {"name": "DOT MCP BROWSER TEST X", "date": "2026-1-1"}):
            with self.assertRaises(ValueError):
                p.preview_test(self.path, args)
        self.assertEqual(self.count("proposals"), 1)

    def test_format_only_no_invented_training_date_guardrail(self):
        for date in ("2000-01-01", "2099-12-31"):
            self.assertEqual(p.preview_test(self.path, {"name": "DOT MCP BROWSER TEST X", "date": date})["proposal"]["date"], date)

    def test_approval_binding_each_field(self):
        for field, value in (("preview_id", "0" * 32), ("proposal_sha256", "0" * 64),
                             ("name", "DOT MCP BROWSER TEST OTHER"), ("date", "2026-10-19")):
            with self.subTest(field=field):
                self.fake_approval(**{field: value})
                self.assertEqual(p.apply_test(self.path, self.args)["status"], "awaiting_owner_confirmation")
                self.assertEqual(self.count("queue"), 0)
                # Fixture cleanup only. Real runtime records must never be removed.
                (self.path / "approvals" / (self.args["preview_id"] + ".json")).unlink()

    def test_expired_future_and_predating_confirmation_rejected(self):
        for offset in (-600, 60):
            current = p.now() + timedelta(seconds=offset)
            self.fake_approval(owner_reply_at=p.stamp(current), recorded_at=p.stamp(current),
                               expires_at=p.stamp(current + timedelta(seconds=300)))
            self.assertEqual(p.apply_test(self.path, self.args)["status"], "awaiting_owner_confirmation")
            (self.path / "approvals" / (self.args["preview_id"] + ".json")).unlink()
        self.assertEqual(self.count("queue"), 0)

    def test_tampered_proposal_blocks_even_with_fake_approval(self):
        self.fake_approval()
        path = self.path / "proposals" / (self.args["preview_id"] + ".json")
        with path.open("ab") as file:
            file.write(b" ")
        with self.assertRaises(p.ProtocolError):
            p.apply_test(self.path, self.args)
        self.assertEqual(self.count("queue"), 0)

    def test_hash_recomputed_for_changed_effects_still_rejected(self):
        body = self.preview["proposal"]
        body["device_delivery"] = True
        raw = p.canonical(body)
        path = self.path / "proposals" / (self.args["preview_id"] + ".json")
        path.write_bytes(raw)
        self.args["proposal_sha256"] = p.digest(raw)
        self.fake_approval()
        with self.assertRaises(p.ProtocolError):
            p.apply_test(self.path, self.args)
        self.assertEqual(self.count("queue"), 0)

    def test_approved_is_only_pending_duplicate_never_redispatches(self):
        self.fake_approval()
        first = p.apply_test(self.path, self.args)
        before = {str(path): path.read_bytes() for part in ("claims", "journal", "queue") for path in (self.path / part).iterdir()}
        second = p.apply_test(self.path, self.args)
        self.assertEqual(first["status"], "pending_parent_cua")
        self.assertFalse(first["external_success"])
        self.assertFalse(second["redispatched"])
        self.assertEqual(self.count("queue"), 1)
        for filename, raw in before.items():
            self.assertEqual(Path(filename).read_bytes(), raw)
            self.assertEqual(stat.S_IMODE(Path(filename).stat().st_mode), 0o400)

    def test_claim_and_journal_precede_queue(self):
        self.fake_approval()
        real_save = p.save_once
        def check(path, raw, immutable=False):
            if path.parent.name == "queue":
                self.assertEqual(self.count("claims"), 1)
                self.assertEqual(self.count("journal"), 1)
            return real_save(path, raw, immutable)
        with patch.object(p, "save_once", side_effect=check):
            p.apply_test(self.path, self.args)

    def test_crash_after_claim_never_queues_on_retry(self):
        self.fake_approval()
        with patch.object(p, "journal", side_effect=OSError("mock crash")):
            with self.assertRaises(OSError):
                p.apply_test(self.path, self.args)
        result = p.apply_test(self.path, self.args)
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse(result["redispatched"])
        self.assertEqual(self.count("claims"), 1)
        self.assertEqual(self.count("queue"), 0)

    def test_execution_claim_receipt_before_possible_write_and_no_retry(self):
        self.fake_approval()
        p.apply_test(self.path, self.args)
        body = p.claim_execution(self.path, self.args, browser_account_key="a" * 64)
        self.assertEqual(body, self.preview["proposal"])
        self.assertEqual(self.count("execution"), 1)
        self.assertTrue((self.path / "journal" / (self.args["preview_id"] + "-external_write_intent.json")).exists())
        self.assertEqual(p.apply_test(self.path, self.args)["status"], "uncertain")
        with self.assertRaises(FileExistsError):
            p.claim_execution(self.path, self.args, browser_account_key="a" * 64)
        self.assertEqual(self.count("queue"), 1)

    def test_partial_and_unknown_never_success(self):
        self.fake_approval()
        p.apply_test(self.path, self.args)
        p.claim_execution(self.path, self.args, browser_account_key="a" * 64)
        p.record_worker_outcome(self.path, self.args, "partial")
        result = p.apply_test(self.path, self.args)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["external_success"])
        self.assertFalse(result["automatic_retry"])
        self.assertEqual(self.count("queue"), 1)
        with self.assertRaises(p.ProtocolError):
            p.record_worker_outcome(self.path, self.args, "success")

    def test_uncertain_receipt_is_retained(self):
        self.fake_approval()
        p.apply_test(self.path, self.args)
        p.claim_execution(self.path, self.args, browser_account_key="a" * 64)
        p.record_worker_outcome(self.path, self.args, "uncertain")
        self.assertEqual(p.apply_test(self.path, self.args)["status"], "uncertain")
        self.assertEqual(self.count("outcomes"), 1)
        self.assertEqual(self.count("execution"), 1)
        self.assertEqual(self.count("queue"), 1)

    def test_tampered_outcome_status_or_binding_never_success(self):
        self.fake_approval()
        p.apply_test(self.path, self.args)
        p.claim_execution(self.path, self.args, browser_account_key="a" * 64)
        p.record_worker_outcome(self.path, self.args, "partial")
        path = self.path / "outcomes" / (self.args["preview_id"] + ".json")
        original = p.read_json(path)
        for change in ({"status": "success"}, {"preview_id": "0" * 32}, {"proposal_sha256": "0" * 64},
                       {"external_success": True}):
            # Simulate the explicitly documented same-UID operator trust limit.
            path.chmod(0o600)
            path.write_bytes(p.canonical({**original, **change}))
            path.chmod(0o400)
            result = p.apply_test(self.path, self.args)
            self.assertEqual(result["status"], "uncertain")
            self.assertFalse(result["external_success"])
            self.assertEqual(self.count("queue"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
