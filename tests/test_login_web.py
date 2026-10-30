"""A real loopback HTTP test with a fake Garmin login; never contacts Garmin."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib import request, error, parse

BOOT = '''
import json, sys
from unittest.mock import patch
from garmin_agent_tools.login_web import serve
class Fake:
    def __init__(self, email, password, is_cn, prompt_mfa):
        assert email=='anonymous@example.invalid' and password=='synthetic-password'
        self.prompt=prompt_mfa
        self.client=self
    def login(self):
        assert self.prompt()=='synthetic-code'
    def dumps(self):
        return json.dumps({'di_token':'fictional-token','di_refresh_token':'fictional-refresh'})
with patch('garminconnect.Garmin',Fake):
    serve('international',int(sys.argv[1]),30)
'''

class PrivateLogin(unittest.TestCase):
    def test_personal_key_form_exchanges_token_without_data_reads(self):
        boot='''
import base64,json,sys,time
from unittest.mock import patch
import requests
from garmin_agent_tools.login_web import serve
def fake(self,method,url,**kwargs):
    assert method=='POST' and url.endswith('/personal-api-key/token')
    assert kwargs['headers']['Authorization']=='ApiKey synthetic-personal-key'
    r=requests.Response();r.status_code=200
    value=base64.urlsafe_b64encode(json.dumps({'exp':time.time()+3600}).encode()).decode().rstrip('=')
    r._content=('anonymous.'+value+'.synthetic').encode();return r
with patch.object(requests.Session,'request',fake):
    serve('international',int(sys.argv[1]),30,backend='personal')
'''
        with tempfile.TemporaryDirectory() as directory:
            sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
            env=dict(os.environ,GARMIN_PERSONAL_HOME=directory)
            proc=subprocess.Popen([sys.executable,'-u','-c',boot,str(port)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                url=proc.stdout.readline().strip();nonce=parse.urlsplit(url).path.removeprefix('/')
                with request.urlopen(url,timeout=3) as opened:
                    self.assertEqual(opened.headers['Referrer-Policy'],'same-origin')
                    page=opened.read().decode()
                self.assertIn('你在 Garmin 登入，工具只接收 Key',page)
                self.assertNotIn('name="password"',page)
                req=request.Request(url,data=parse.urlencode({'csrf':nonce,'api_key':'synthetic-personal-key'}).encode(),headers={'Origin':f'http://127.0.0.1:{port}','Content-Type':'application/x-www-form-urlencoded'})
                request.urlopen(req,timeout=3).close()
                for _ in range(30):
                    page=request.urlopen(url,timeout=3).read().decode()
                    if 'token 已取得並儲存' in page:break
                    time.sleep(.03)
                self.assertIn('MCP 資料讀取：尚未驗證',page)
                self.assertNotIn('synthetic-personal-key',page)
                self.assertEqual((Path(directory)/'key.json').stat().st_mode&0o777,0o600)
                self.assertEqual((Path(directory)/'token.json').stat().st_mode&0o777,0o600)
            finally:
                proc.terminate();proc.wait(timeout=5)
                stderr=proc.stderr.read();proc.stdout.close();proc.stderr.close()
                self.assertNotIn('synthetic-personal-key',stderr)

    def test_rate_limit_is_actionable_and_does_not_offer_retry(self):
        boot=BOOT.replace("assert self.prompt()=='synthetic-code'", "from garmin_agent_tools.auth import AuthError; raise AuthError('rate_limited')")
        with tempfile.TemporaryDirectory() as directory:
            sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
            env=dict(os.environ,GARMIN_AGENT_HOME=str(Path(directory).resolve()))
            proc=subprocess.Popen([sys.executable,'-u','-c',boot,str(port)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                url=proc.stdout.readline().strip();nonce=parse.urlsplit(url).path.removeprefix('/')
                page=request.urlopen(url,timeout=3).read().decode()
                self.assertIn('這是非官方工具的本機表單',page)
                self.assertIn('專用 Key 表單',page)
                data=parse.urlencode({'csrf':nonce,'email':'anonymous@example.invalid','password':'synthetic-password'}).encode()
                req=request.Request(url,data=data,headers={'Origin':f'http://127.0.0.1:{port}','Content-Type':'application/x-www-form-urlencoded'})
                request.urlopen(req,timeout=3).close()
                for _ in range(30):
                    page=request.urlopen(url,timeout=3).read().decode()
                    if 'rate_limited' in page:break
                    time.sleep(.03)
                self.assertIn('rate_limited',page)
                self.assertIn('尚無法判斷帳密正誤',page)
                self.assertIn('不會自動重試',page)
                self.assertNotIn('<form',page)
                self.assertNotIn('synthetic-password',page)
                self.assertFalse((Path(directory)/'.env').exists())
            finally:
                proc.terminate();proc.wait(timeout=5)
                proc.stdout.close();proc.stderr.close()

    def test_host_origin_nonce_mfa_and_private_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
            env=dict(os.environ,GARMIN_AGENT_HOME=str(Path(directory).resolve()))
            proc=subprocess.Popen([sys.executable,'-u','-c',BOOT,str(port)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                url=proc.stdout.readline().strip();self.assertTrue(url.startswith('http://127.0.0.1:'))
                nonce=parse.urlsplit(url).path.removeprefix('/')
                origin=f'http://127.0.0.1:{port}'
                def post(values,site=origin):
                    req=request.Request(url,data=parse.urlencode(values).encode(),headers={'Origin':site,'Content-Type':'application/x-www-form-urlencoded'})
                    return request.urlopen(req,timeout=3).read().decode()
                for refused_origin in ('https://hostile.invalid','null'):
                    with self.assertRaises(error.HTTPError) as caught:
                        post({'csrf':nonce,'email':'anonymous@example.invalid','password':'synthetic-password'},refused_origin)
                    self.assertEqual(caught.exception.code,403)
                    self.assertIn('local_form_refused',caught.exception.read().decode())
                with self.assertRaises(error.HTTPError) as caught:
                    post({'csrf':'wrong','email':'anonymous@example.invalid','password':'synthetic-password'})
                self.assertEqual(caught.exception.code,403)
                post({'csrf':nonce,'email':'anonymous@example.invalid','password':'synthetic-password'})
                for _ in range(30):
                    body=request.urlopen(url,timeout=3).read().decode()
                    if 'Garmin verification code' in body:break
                    time.sleep(.03)
                self.assertIn('Garmin verification code',body)
                post({'csrf':nonce,'otp':'synthetic-code'})
                for _ in range(30):
                    body=request.urlopen(url,timeout=3).read().decode()
                    if 'Login completed' in body:break
                    time.sleep(.03)
                self.assertIn('Login completed',body)
                saved=(Path(directory)/'.env').read_text()
                self.assertNotIn('synthetic-password',saved);self.assertNotIn('synthetic-code',saved)
                self.assertEqual((Path(directory)/'.env').stat().st_mode&0o777,0o600)
            finally:
                proc.terminate();proc.wait(timeout=5)
                stderr=proc.stderr.read();proc.stdout.close();proc.stderr.close()
                self.assertNotIn('synthetic-password',stderr);self.assertNotIn('synthetic-code',stderr)

if __name__=='__main__':unittest.main()
