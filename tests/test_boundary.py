import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from garmin_agent_tools import core

class Fake:
    display_name='anonymous-athlete'
    class Client:
        domain='garmin.com'
    client=Client()
    def __init__(self):self.uploads=0;self.schedules=0;self.workout=None;self.rows=[]
    def upload_workout(self,payload):self.uploads+=1;self.workout=dict(payload,workoutId=123);return {'workoutId':123}
    def get_workout_by_id(self,wid):return self.workout
    def get_scheduled_workouts(self,y,m):return {'calendarItems':self.rows}
    def schedule_workout(self,wid,date):
        self.schedules+=1;self.rows=[{'id':456,'workoutId':wid,'date':date,'title':self.workout['workoutName'],'itemType':'workout'}];return {'workoutScheduleId':456}

class Boundary(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.patch=patch.dict(os.environ,{'GARMIN_AGENT_HOME':self.temp.name});self.patch.start()
    def tearDown(self):self.patch.stop();self.temp.cleanup()
    def test_retry_does_not_duplicate_workout_or_calendar(self):
        g=Fake();p=core.preview_test(g,'anonymous-test','2026-10-15',None)
        a=core.apply_test(g,p['approval_sha256']);b=core.apply_test(g,p['approval_sha256'])
        self.assertEqual((g.uploads,g.schedules),(1,1));self.assertEqual(a,b)
    def test_changed_preview_or_account_cannot_write(self):
        g=Fake();p=core.preview_test(g,'anonymous',None,None);g.display_name='another-athlete'
        with self.assertRaises(core.ToolError):core.apply_test(g,p['approval_sha256'])
        self.assertEqual(g.uploads,0)
    def test_uncertain_upload_is_not_repeated(self):
        g=Fake();p=core.preview_test(g,'anonymous',None,None)
        def uncertain(payload):g.uploads+=1;raise TimeoutError('credential-marker-must-not-leak')
        g.upload_workout=uncertain
        with self.assertRaises(TimeoutError):core.apply_test(g,p['approval_sha256'])
        with self.assertRaises(core.ToolError):core.apply_test(g,p['approval_sha256'])
        self.assertEqual(g.uploads,1)
    def test_readback_mismatch_blocks_schedule(self):
        g=Fake();p=core.preview_test(g,'anonymous','2026-10-15',None)
        original=g.get_workout_by_id
        def wrong(wid):r=original(wid);r['workoutSegments'][0]['workoutSteps'][0]['endConditionValue']=999;return r
        g.get_workout_by_id=wrong
        with self.assertRaises(core.ToolError):core.apply_test(g,p['approval_sha256'])
        self.assertEqual(g.schedules,0)
    def test_credentials_private_and_no_password_saved(self):
        class G:
            class C:
                def dumps(self):return json.dumps({'di_token':'fictional-test-token','di_refresh_token':'fictional-refresh'})
            client=C()
        core.save_session(G(),'international');p=Path(self.temp.name)/'.env'
        self.assertEqual(p.stat().st_mode&0o777,0o600);self.assertNotIn('password',p.read_text())
    def test_symlink_state_rejected(self):
        link=Path(self.temp.name)/'linked';link.symlink_to(self.temp.name)
        with patch.dict(os.environ,{'GARMIN_AGENT_HOME':str(link)}):
            with self.assertRaises(core.ToolError):core.state_home()
    def test_unknown_source_remains_unknown(self):
        g=Fake();g.get_training_status=lambda date:{'mostRecentTrainingStatus':None}
        g.get_activities=lambda a,b:[];g.get_training_readiness=lambda date:[];g.get_devices=lambda:[]
        result=core.snapshot(g,'2026-10-15');self.assertEqual(result['endpoints']['training_status']['data']['availability'],'unknown')

if __name__=='__main__':unittest.main()
