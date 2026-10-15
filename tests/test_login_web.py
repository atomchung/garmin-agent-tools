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
                with self.assertRaises(error.HTTPError) as caught:
                    post({'csrf':nonce,'email':'anonymous@example.invalid','password':'synthetic-password'},'https://hostile.invalid')
                self.assertEqual(caught.exception.code,403)
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
