"""Official Personal API contracts with anonymous HTTP doubles, never Garmin."""
import asyncio
import base64
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import requests
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from garmin_agent_tools import auth, personal


def token(expiry=None):
    payload=base64.urlsafe_b64encode(json.dumps({'exp':expiry or time.time()+3600}).encode()).decode().rstrip('=')
    return 'synthetic.'+payload+'.not-a-real-signature'


def response(data, status=200, raw=False):
    value=requests.Response(); value.status_code=status
    value._content=(data if raw else json.dumps(data)).encode()
    return value


BOOT='''
import base64,json,os,time
from pathlib import Path
from unittest.mock import patch
import requests
def fake(self,method,url,**kwargs):
    assert url.startswith('https://personal.connectapi.garmin.com/')
    assert kwargs['allow_redirects'] is False
    with (Path(os.environ['GARMIN_PERSONAL_HOME'])/'calls.txt').open('a') as stream: stream.write(method+'\\n')
    r=requests.Response();r.status_code=200
    if method=='POST':
        assert kwargs['headers']['Authorization']=='ApiKey synthetic-key'
        payload=base64.urlsafe_b64encode(json.dumps({'exp':time.time()+3600}).encode()).decode().rstrip('=')
        r._content=('synthetic.'+payload+'.anonymous').encode()
    else:
        day=kwargs['params']['startDate'];assert day==kwargs['params']['endDate']
        r._content=json.dumps([{'startTimeLocal':day+' 08:00:00','activityType':{'typeKey':'running'},'duration':12345,'distance':67890,'averageHR':None}]).encode()
    return r
from garmin_agent_tools import personal
with patch.object(requests.Session,'request',fake): personal.run_mcp()
'''


class Personal(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.patch=patch.dict(os.environ,{'GARMIN_PERSONAL_HOME':self.temp.name})
        self.patch.start()
    def tearDown(self):
        self.patch.stop();self.temp.cleanup()

    def test_key_exchange_is_private_and_does_not_read_data(self):
        with patch.object(requests.Session,'request',return_value=response(token(),raw=True)) as call:
            report=personal.configure_key('synthetic-key')
        self.assertEqual(call.call_count,1)
        self.assertEqual(call.call_args.args[:2],('POST',personal.ORIGIN+personal.TOKEN_PATH))
        self.assertIs(call.call_args.kwargs['allow_redirects'],False)
        self.assertFalse(report['data_access_verified'])
        for name in ['key.json','token.json']:
            self.assertEqual((Path(self.temp.name)/name).stat().st_mode&0o777,0o600)
        self.assertNotIn('synthetic-key',json.dumps(report))

    def test_cached_token_is_reused_and_expired_token_is_replaced(self):
        personal._save('key.json',{'key':'synthetic-key'})
        personal._save('token.json',{'fingerprint':personal._fingerprint('synthetic-key'),'access_token':token()})
        with patch.object(requests.Session,'request') as call:
            personal.access_token()
            call.assert_not_called()
        personal._save('token.json',{'fingerprint':personal._fingerprint('synthetic-key'),'access_token':token(time.time()-5)})
        with patch.object(requests.Session,'request',return_value=response(token(),raw=True)) as call:
            personal.access_token()
        self.assertEqual(call.call_count,1)

    def test_429_stops_subsequent_process_style_attempts_without_retry(self):
        with patch.object(requests.Session,'request',return_value=response('provider-secret',429,raw=True)) as call:
            with self.assertRaises(auth.AuthError) as caught:
                personal.configure_key('synthetic-key')
            with self.assertRaises(auth.AuthError): personal.access_token()
        self.assertEqual(call.call_count,1)
        self.assertEqual(caught.exception.code,'rate_limited')
        self.assertNotIn('provider-secret',json.dumps(caught.exception.report()))
        self.assertTrue(personal.connection_status()['attempt_stopped'])

    def test_snapshot_is_date_filtered_paginated_and_never_discloses_measurements(self):
        batch=[{'activityId':n,'startTimeLocal':'2026-10-16 08:00:00','duration':12345,'distance':67890} for n in range(100)]
        with patch.object(personal,'access_token',return_value='synthetic-token'), patch.object(personal,'_request',side_effect=[response(batch),response([])]) as call:
            value=personal.snapshot('2026-10-16')
        self.assertEqual(call.call_count,2)
        self.assertEqual(call.call_args.kwargs['params'],{'start':100,'limit':100,'startDate':'2026-10-16','endDate':'2026-10-16'})
        self.assertEqual(value['endpoints']['activities']['activity_count'],100)
        self.assertTrue(value['endpoints']['activities']['count_complete'])
        self.assertNotIn('12345',json.dumps(value));self.assertNotIn('67890',json.dumps(value))
        self.assertEqual(value['endpoints']['activities']['fields']['average_hr'],'missing')

    def test_unknown_response_is_not_a_zero_activity_day(self):
        with patch.object(personal,'access_token',return_value='synthetic'), patch.object(personal,'_request',return_value=response(None)):
            with self.assertRaises(auth.AuthError):personal.snapshot('2026-10-16')

    def test_conflicting_dates_and_ignored_pagination_are_incomplete(self):
        rows=[{'activityId':n,'startTimeLocal':'2026-10-15 08:00:00'} for n in range(100)]
        with patch.object(personal,'access_token',return_value='synthetic'), patch.object(personal,'_request',return_value=response(rows)) as call:
            value=personal.snapshot('2026-10-16')['endpoints']['activities']
        self.assertEqual(call.call_count,2)
        self.assertFalse(value['count_complete']);self.assertEqual(value['count_kind'],'lower_bound')

    def test_two_real_sdk_processes_reuse_the_same_saved_token(self):
        personal._save('key.json',{'key':'synthetic-key'})
        async def invoke():
            params=StdioServerParameters(command=os.sys.executable,args=['-u','-c',BOOT],env=dict(os.environ))
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as session:
                    info=await session.initialize();self.assertIn('Read-only',info.instructions)
                    tools=await session.list_tools()
                    self.assertEqual({t.name for t in tools.tools},{'read_snapshot','get_connection_status'})
                    self.assertTrue(all(t.annotations.readOnlyHint for t in tools.tools))
                    result=await session.call_tool('read_snapshot',{'date':'2026-10-16'})
                    self.assertFalse(result.isError)
                    data=result.structuredContent or json.loads(result.content[0].text)
                    self.assertEqual(data['endpoints']['activities']['activity_count'],1)
                    self.assertNotIn('12345',json.dumps(data))
        asyncio.run(invoke());asyncio.run(invoke())
        self.assertEqual((Path(self.temp.name)/'calls.txt').read_text().splitlines(),['POST','GET','GET'])


if __name__=='__main__':unittest.main()
