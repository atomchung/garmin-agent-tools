"""Official Garmin Personal API: private credentials and counts-only daily reads.

HTTP contracts: Garmin's Personal API docs and garmin/connect-personal-api-python.
No browser cookie access, password login, provider writes, or automatic retries.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time

from . import auth, core
from .browser.state import private_location

ORIGIN = 'https://personal.connectapi.garmin.com'
TOKEN_PATH = '/personalapitoken-service/personal-api/personal-api-key/token'
ACTIVITIES_PATH = '/activitylist-service/activities/search/activities'
KEY_PAGE = 'https://connect.garmin.com/app/settings/personalApiToken'


def home():
    root = private_location(os.environ.get('GARMIN_PERSONAL_HOME', str(Path.home()/'.local/share/garmin-agent-tools-personal')))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = root.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise core.ToolError('Personal API state must be owned by this user with mode 0700')
    return root


def _load(name):
    path = home()/name
    if not path.exists():
        return None
    try:
        value = json.loads(core.read_private(path))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise core.ToolError('Private Personal API state is invalid; no credentials disclosed') from None


def _save(name, value):
    core.private_write(home()/name, json.dumps(value, separators=(',', ':'))+'\n')


def _request(method, path, *, authorization, params=None):
    import requests
    stopped = _load('stopped.json')
    if stopped:
        code = stopped.get('code')
        raise auth.AuthError(code if code in ('rate_limited','access_denied','personal_key_rejected') else 'personal_request_failed')
    try:
        with requests.Session() as session:
            session.mount('https://', requests.adapters.HTTPAdapter(max_retries=0))
            response = session.request(method, ORIGIN+path, headers={'Authorization':authorization,'Accept':'*/*'},
                                       params=params, timeout=30, allow_redirects=False)
    except requests.exceptions.RequestException:
        raise auth.AuthError('network_error', stage='login' if method=='POST' else 'read') from None
    code = response.status_code
    if code == 429:
        _save('stopped.json', {'code':'rate_limited'})
        raise auth.AuthError('rate_limited', stage='login' if method=='POST' else 'read', http_status=429)
    if code in (401,403):
        reason = 'personal_key_rejected' if method=='POST' else 'access_denied'
        _save('stopped.json', {'code':reason})
        raise auth.AuthError(reason,
                             stage='login' if method=='POST' else 'read', http_status=code)
    if not 200 <= code < 300:
        raise auth.AuthError('personal_request_failed', stage='login' if method=='POST' else 'read')
    return response


def _key():
    data = _load('key.json')
    value = data.get('key') if data else None
    if not isinstance(value,str) or not value.strip():
        raise auth.AuthError('personal_key_required')
    return value


def _fingerprint(key):
    return hashlib.sha256((ORIGIN+'\0'+key).encode()).hexdigest()


def _expiry(token):
    try:
        payload = token.split('.')[1]
        value = json.loads(base64.urlsafe_b64decode(payload+'='*(-len(payload)%4)))['exp']
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value <= 0:
            raise ValueError()
        return float(value)
    except (ValueError, TypeError, KeyError, IndexError):
        raise auth.AuthError('personal_response_invalid') from None


def access_token():
    """Reuse this key's unexpired token; never refresh/retry a rejected request."""
    key = _key()
    cached = _load('token.json')
    if cached and cached.get('fingerprint') == _fingerprint(key) and isinstance(cached.get('access_token'),str):
        if _expiry(cached['access_token']) > time.time():
            return cached['access_token']
    response = _request('POST', TOKEN_PATH, authorization='ApiKey '+key)
    token = response.text.strip()
    expires_at = _expiry(token)  # A cache hint from Garmin's HTTPS token response, not signature verification.
    if expires_at <= time.time():
        raise auth.AuthError('personal_response_invalid')
    _save('token.json', {'fingerprint':_fingerprint(key),'access_token':token,'expires_at':expires_at})
    return token


def configure_key(value):
    """Only the private form/TTY calls this; never expose it as an MCP tool."""
    if not isinstance(value,str) or not value.strip() or len(value)>8192 or '\n' in value.strip() or '\r' in value:
        raise auth.AuthError('personal_key_required')
    _save('key.json', {'key':value.strip()})
    # Only an explicit private user setup starts a new attempt after a refusal.
    (home()/'stopped.json').unlink(missing_ok=True)
    access_token()
    return {'status':'success','stage':'token_saved','backend':'personal',
            'data_access_verified':False,'workouts_written':False}


def connection_status():
    """Inspect local files only; never a provider call or live-access claim."""
    key = _key() if (home()/'key.json').exists() else None
    token = _load('token.json')
    valid = False
    if key and token and token.get('fingerprint') == _fingerprint(key) and isinstance(token.get('access_token'),str):
        try:
            valid = _expiry(token['access_token']) > time.time()
        except auth.AuthError:
            pass
    return {'backend':'personal','key_configured':key is not None,'unexpired_token_cached':valid,
            'attempt_stopped':(home()/'stopped.json').exists(),
            'live_access_verified':False,'read_only':True}


def _presence(rows, getter):
    if not rows:
        return 'no_rows'
    flags = [getter(row) is not None for row in rows]
    return 'present' if all(flags) else 'missing' if not any(flags) else 'mixed'


def snapshot(date):
    """One date's activities: paginate, then return only counts and field presence."""
    if not isinstance(date,str) or dt.date.fromisoformat(date).isoformat() != date:
        raise core.ToolError('Use an explicit YYYY-MM-DD calendar date')
    token = access_token()
    rows = []
    complete = False
    seen = set()
    for page in range(20):
        response = _request('GET', ACTIVITIES_PATH, authorization='Bearer '+token,
                            params={'start':page*100,'limit':100,'startDate':date,'endDate':date})
        try:
            batch = response.json()
        except ValueError:
            raise auth.AuthError('personal_response_invalid',stage='read') from None
        if not isinstance(batch,list) or any(not isinstance(r,dict) for r in batch):
            raise auth.AuthError('personal_response_invalid',stage='read')
        signature = hashlib.sha256(json.dumps(batch,sort_keys=True).encode()).digest()
        if batch and signature in seen:
            break  # A server ignoring pagination must not produce invented totals.
        seen.add(signature)
        rows.extend(batch)
        if len(batch)<100:
            complete=True
            break
    # Date filter semantics belong to Garmin. An observed conflicting date makes
    # completeness unknown; never silently count a different day's activities.
    dated = [r for r in rows if isinstance(r.get('startTimeLocal'),str) and r['startTimeLocal'].startswith(date)]
    if len(dated)!=len(rows):
        complete=False
    fields = {'date':lambda r:r.get('startTimeLocal'),
              'sport':lambda r:(r.get('activityType') or {}).get('typeKey') if isinstance(r.get('activityType') or {},dict) else None,
              'duration_seconds':lambda r:r.get('duration'),'distance_meters':lambda r:r.get('distance'),
              'average_hr':lambda r:r.get('averageHR')}
    return {'date':date,'requested_timezone':'Asia/Taipei','date_basis':'Garmin startTimeLocal calendar date',
            'timezone_independently_verified':False,'backend':'personal','values_disclosed':False,
            'endpoints':{'activities':{'status':'ok','activity_count':len(dated),'count_complete':complete,
                                     'count_kind':'exact' if complete else 'lower_bound',
                                     'fields':{name:_presence(dated,getter) for name,getter in fields.items()}},
                         **{name:{'status':'not_implemented_by_this_backend'} for name in ('training_readiness','training_status','devices')}}}


def run_mcp():
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations
    server = FastMCP('Garmin Personal Read', log_level='CRITICAL', instructions=(
        'Read-only local Garmin Personal API tools. Credential entry is outside MCP. '
        'Return counts and field presence only; no health measurements. Stop on an access error or 429; '
        'do not retry, change backend, or infer a wrong password. Local status is not live verification. '
        'No workout, calendar or watch writes are exposed.'))
    def safe(call):
        try:
            return call()
        except auth.AuthError as exc:
            return exc.report()
        except Exception:
            return {'status':'failed','code':'local_or_response_error','automatic_retry':False}
    @server.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False))
    def read_snapshot(date:str)->dict:
        """Read one explicit Garmin calendar date; return activity count and field presence, never measurements. No writes."""
        return safe(lambda:snapshot(date))
    @server.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False))
    def get_connection_status()->dict:
        """Inspect local credential/cache presence without contacting Garmin; does not verify current provider access."""
        return safe(connection_status)
    server.run(transport='stdio')
