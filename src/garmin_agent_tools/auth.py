"""Safe authentication diagnostics and a no-fallback boundary for denied requests."""
from __future__ import annotations

import contextlib
import contextvars
import functools
import re
import threading

_stage = contextvars.ContextVar('garmin_auth_stage', default=None)
_install_lock = threading.Lock()

MESSAGES = {
    'personal_key_required': ('A Garmin Personal API Key has not been configured.',
                              'Create it yourself on the Garmin website and enter it only in the private local Personal API form; never put it in chat.'),
    'personal_key_rejected': ('Garmin refused this Personal API Key. This does not establish a password problem.',
                              'This attempt is stopped. Check the key status and expiry privately on Garmin before explicitly configuring it again.'),
    'personal_response_invalid': ('Garmin returned an unexpected token or data response.',
                                  'Stop this attempt; no provider response or credential is disclosed.'),
    'personal_request_failed': ('The official Personal API request did not complete successfully.',
                                'Stop this attempt and inspect the local connection status; do not retry automatically.'),
    'rate_limited': ('Garmin limited this request (429). This does not establish whether your password is correct.',
                     'Stop this attempt. Do not retry automatically or switch login routes. Garmin did not provide a verified retry time.'),
    'invalid_credentials': ('Garmin explicitly reported that the username or password does not match.',
                            'Check the email and password privately. Use Garmin account recovery if needed; do not share credentials in chat.'),
    'account_blocked': ('Garmin returned an explicit account lock or suspension signal.',
                        'Stop this attempt and follow Garmin account-support instructions. Do not change routes to bypass the block.'),
    'access_denied': ('Garmin refused access. This alone does not establish a wrong password or a locked account.',
                      'Stop this attempt. Inspect the normal Garmin website yourself when appropriate; website access does not authenticate this tool.'),
    'network_error': ('The request could not complete because of a connection, TLS, or timeout problem.',
                      'Check your connection and Garmin service status. Password correctness remains unknown.'),
    'authentication_failed': ('Program authentication did not complete. The cause is not established.',
                             'Compare with your own normal Garmin website access. Do not assume a password problem from this result.'),
    'mfa_rejected': ('Garmin explicitly rejected the verification code.',
                     'This attempt has stopped. Check the verification method privately before deciding whether to start a new attempt.'),
    'mfa_timeout': ('No verification code was received before the private form timed out.',
                    'This attempt has stopped. No conclusion about password correctness or data access follows from this timeout.'),
    'credential_override': ('An environment credential override would replace the account or session being tested.',
                            'Unset GARMIN_AGENT_SESSION and GARMINTOKENS in your private terminal before explicitly starting a new login.'),
    'session_saved': ('Login completed and the session was saved on this computer.',
                      'A successful snapshot is still required to verify data access; another process must verify session reuse.'),
    'storage_error': ('The local session or diagnostic file could not be saved.',
                      'Check the private state directory permissions and free disk space. This result does not establish a password error.'),
}


class AuthError(Exception):
    """Only a fixed, public diagnosis; never a provider body or exception string."""
    def __init__(self, code='authentication_failed', *, stage='login', http_status=None):
        self.code = code if code in MESSAGES else 'authentication_failed'
        self.stage = stage if stage in ('login', 'session_reuse', 'read') else 'login'
        self.http_status = http_status if http_status in (401, 403, 429) else None
        super().__init__(MESSAGES[self.code][0])

    def report(self):
        message, next_step = MESSAGES[self.code]
        return {'status': 'failed', 'stage': self.stage, 'code': self.code,
                'http_status': self.http_status, 'credentials_verified': False if self.code == 'invalid_credentials' else None,
                'account_blocked': True if self.code == 'account_blocked' else None,
                'automatic_retry': False, 'message': message, 'next_step': next_step}


class _Abort(BaseException):
    # SDK strategies and snapshot's optional reads catch Exception. This private
    # signal must reach the enclosing product boundary before becoming AuthError.
    def __init__(self, diagnosis):
        self.diagnosis = diagnosis


def _check_response(response):
    stage = _stage.get()
    if stage is None:
        return response
    status = getattr(response, 'status_code', None)
    code = {429: 'rate_limited', 401: 'access_denied', 403: 'access_denied'}.get(status)
    # Only explicit machine-readable provider signals establish credential/code
    # rejection or account blocking. Never return, persist, or quote the payload.
    try:
        body = response.json()
    except Exception:
        body = {}
        if re.search(r'cf-chl-|g-recaptcha|h-captcha|account (?:is |has been )?(?:locked|blocked|suspended)',
                     getattr(response, 'text', ''), re.I):
            code = code or 'access_denied'
    if isinstance(body, dict):
        error = body.get('error')
        state = body.get('responseStatus')
        signal = state.get('type') if isinstance(state, dict) else None
        if status == 429 or (isinstance(error, dict) and str(error.get('status-code')) == '429'):
            code = 'rate_limited'
        elif signal == 'INVALID_USERNAME_PASSWORD':
            code = 'invalid_credentials'
        elif signal in ('ACCOUNT_LOCKED', 'ACCOUNT_BLOCKED', 'ACCOUNT_SUSPENDED'):
            code = 'account_blocked'
        elif signal in ('INVALID_MFA_CODE', 'INVALID_VERIFICATION_CODE'):
            code = 'mfa_rejected'
    if code:
        raise _Abort(AuthError(code, stage=stage, http_status=status))
    return response


def _guard(original):
    if getattr(original, '_garmin_auth_guard', False) is True:
        return original
    @functools.wraps(original)
    def call(*args, **kwargs):
        try:
            response = original(*args, **kwargs)
        except Exception as exc:
            from requests.exceptions import ConnectionError, Timeout
            from curl_cffi.requests.exceptions import RequestException
            if _stage.get() is not None and isinstance(exc, (OSError, ConnectionError, Timeout, RequestException)):
                raise _Abort(AuthError('network_error', stage=_stage.get())) from None
            raise
        return _check_response(response)
    call._garmin_auth_guard = True
    return call


def _install_guards():
    # ContextVar confines checks to the calling operation/thread; web UI requests
    # and unrelated requests in a host process are not classified or interrupted.
    import requests
    from curl_cffi import requests as cffi
    with _install_lock:
        requests.Session.send = _guard(requests.Session.send)
        cffi.Session.request = _guard(cffi.Session.request)


@contextlib.contextmanager
def protected(stage='login'):
    """Stop SDK fallback immediately, then expose a secret-free diagnosis."""
    _install_guards()
    token = _stage.set(stage)
    try:
        yield
    except _Abort as exc:
        raise exc.diagnosis from None
    finally:
        _stage.reset(token)


def login(client, tokenstore=None, *, reuse=False):
    """Authenticate once; generic SDK errors do not establish password failure."""
    stage = 'session_reuse' if reuse else 'login'
    try:
        with protected(stage):
            if reuse:
                client.login(tokenstore)
            else:
                client.login()
    except AuthError:
        raise
    except Exception:
        raise AuthError(stage=stage) from None
