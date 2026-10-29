"""Anonymous authentication failures; never contact Garmin or read owner state."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests
from curl_cffi import requests as cffi
from garminconnect import Garmin
from garmin_agent_tools import auth, cli, core


def response(status=200, body=None, text=''):
    return SimpleNamespace(status_code=status, json=lambda: body or {}, text=text)


class Authentication(unittest.TestCase):
    def test_actual_sdk_does_not_fall_back_after_first_429(self):
        with patch.object(cffi.Session, 'request', return_value=response(429)) as request:
            with self.assertRaises(auth.AuthError) as caught:
                auth.login(Garmin(email='anonymous@example.invalid', password='synthetic'))
        self.assertEqual(request.call_count, 1)
        self.assertEqual(caught.exception.report()['code'], 'rate_limited')
        self.assertIsNone(caught.exception.report()['credentials_verified'])

    def test_only_explicit_rejection_establishes_wrong_credentials(self):
        for body, expected in [({}, None), ({'responseStatus': {'type': 'INVALID_USERNAME_PASSWORD'}}, False)]:
            with self.subTest(body=body), patch.object(requests.Session, 'send', return_value=response(401, body)):
                with self.assertRaises(auth.AuthError) as caught:
                    with auth.protected():
                        requests.get('https://anonymous.invalid')
                self.assertIs(caught.exception.report()['credentials_verified'], expected)

    def test_body_rate_limit_and_explicit_account_lock_stop(self):
        for body, code in [({'error': {'status-code': '429'}}, 'rate_limited'),
                           ({'responseStatus': {'type': 'ACCOUNT_LOCKED'}}, 'account_blocked'),
                           ({'responseStatus': {'type': 'INVALID_MFA_CODE'}}, 'mfa_rejected')]:
            with self.subTest(code=code), patch.object(requests.Session, 'send', return_value=response(200, body)):
                with self.assertRaises(auth.AuthError) as caught:
                    with auth.protected():
                        requests.get('https://anonymous.invalid')
                self.assertEqual(caught.exception.code, code)

    def test_network_failure_stops_actual_sdk_without_password_claim(self):
        with patch.object(cffi.Session, 'request', side_effect=TimeoutError('secret-provider-marker')) as request:
            with self.assertRaises(auth.AuthError) as caught:
                auth.login(Garmin(email='anonymous@example.invalid', password='synthetic'))
        self.assertEqual(request.call_count, 1)
        self.assertEqual(caught.exception.code, 'network_error')
        self.assertNotIn('secret-provider-marker', json.dumps(caught.exception.report()))

    def test_local_program_error_is_not_reported_as_a_network_failure(self):
        with patch.object(cffi.Session, 'request', side_effect=ValueError('synthetic-local-bug')):
            with self.assertRaises(ValueError):
                with auth.protected():
                    cffi.Session().get('https://anonymous.invalid')

    def test_html_challenge_is_not_retried(self):
        fake = response()
        fake.json = Mock(side_effect=ValueError('not JSON'))
        fake.text = '<html><div class="g-recaptcha">secret-marker</div></html>'
        with patch.object(requests.Session, 'send', return_value=fake):
            with self.assertRaises(auth.AuthError) as caught:
                with auth.protected():
                    requests.get('https://anonymous.invalid')
        self.assertEqual(caught.exception.code, 'access_denied')

    def test_snapshot_stops_before_any_later_endpoint(self):
        g = Mock()
        g.client.domain = 'garmin.com'
        g.get_activities.side_effect = lambda *args: requests.get('https://anonymous.invalid')
        with patch.object(requests.Session, 'send', return_value=response(429)):
            with self.assertRaises(auth.AuthError) as caught:
                core.snapshot(g, '2026-01-01')
        self.assertEqual(caught.exception.stage, 'read')
        g.get_training_readiness.assert_not_called()
        g.get_training_status.assert_not_called()
        g.get_devices.assert_not_called()

    def test_guards_do_not_affect_other_threads_or_later_requests(self):
        results = []
        with patch.object(requests.Session, 'send', return_value=response(429)):
            with auth.protected():
                thread = threading.Thread(target=lambda: results.append(requests.get('https://anonymous.invalid').status_code))
                thread.start(); thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
            results.append(requests.get('https://anonymous.invalid').status_code)
        self.assertEqual(results, [429, 429])

    def test_safe_failure_saved_and_doctor_never_authenticates(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'GARMIN_AGENT_HOME': directory}):
            with patch('garminconnect.Garmin') as constructor:
                constructor.return_value.login.side_effect = auth.AuthError('rate_limited')
                with self.assertRaises(auth.AuthError):
                    core.private_login('anonymous@example.invalid', 'secret-marker', 'international', lambda: 'secret-code')
                constructor.return_value.login.reset_mock()
                result = core.doctor()
                constructor.return_value.login.assert_not_called()
            self.assertFalse((Path(directory) / '.env').exists())
            saved = Path(directory) / 'last-login.json'
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
            for secret in ('secret-marker', 'secret-code', 'anonymous@example.invalid'):
                self.assertNotIn(secret, saved.read_text())
            self.assertEqual(result['last_login']['code'], 'rate_limited')
            self.assertFalse(result['last_login']['is_live_check'])

    def test_cli_returns_actionable_safe_failure(self):
        stderr = io.StringIO()
        with patch.object(cli.sys, 'argv', ['garmin-agent', 'login']), patch.object(cli.sys.stdin, 'isatty', return_value=True), \
                patch('builtins.input', return_value='anonymous@example.invalid'), patch.object(cli.getpass, 'getpass', return_value='synthetic'), \
                patch.object(core, 'private_login', side_effect=auth.AuthError('rate_limited')), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                cli.main()
        self.assertEqual(caught.exception.code, 1)
        self.assertEqual(json.loads(stderr.getvalue())['code'], 'rate_limited')

    def test_reuse_supplies_saved_session_without_prompting_for_password(self):
        g = Mock()
        auth.login(g, 'synthetic-token-json', reuse=True)
        g.login.assert_called_once_with('synthetic-token-json')

    def test_private_login_does_not_silently_use_an_environment_session(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'GARMIN_AGENT_HOME': directory, 'GARMINTOKENS': 'synthetic-private-path'}):
            with patch('garminconnect.Garmin') as constructor, self.assertRaises(auth.AuthError) as caught:
                core.private_login('anonymous@example.invalid', 'synthetic', 'international', lambda: 'synthetic')
            constructor.assert_not_called()
            self.assertEqual(caught.exception.code, 'credential_override')


if __name__ == '__main__':
    unittest.main()
