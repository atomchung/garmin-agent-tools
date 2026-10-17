"""Run the packaged worker against fake tabs, never a real browser."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from garmin_agent_tools.browser import write_protocol as p


class BrowserWorker(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is required for fake-tab worker checks')
    def test_documented_reload_loading_retry_access_stop_and_fingerprint_parity(self):
        root = Path(__file__).resolve().parent.parent
        expected = p.account_key_from_profile_url('https://connect.garmin.com/app/profile/ANONYMOUS')
        result = subprocess.run([shutil.which('node'), str(root / 'tests/browser_worker_cases.mjs'),
            str(root / 'src/garmin_agent_tools/browser/browser_worker.js'), expected],
            text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout),
                         {'synthetic_offline_only':True, 'fake_tab_cases':4, 'fingerprint_parity':True})
