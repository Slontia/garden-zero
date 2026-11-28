#!/usr/bin/env python3
"""Isolated schedule, interruption and registration contracts; no real Docker."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import datetime,json,subprocess,tempfile,threading,time,unittest
from unittest.mock import patch
import nas_backup_control as control
import nas_apps

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.h=Path(self.tmp.name);(self.h/'state').mkdir();self.c={'host':str(self.h)}
 def test_settings_validate_before_persisting(self):
  for values in ({'time':'25:00'},{'max_pause_minutes':True},{'max_pause_minutes':0}):
   with self.assertRaises(RuntimeError):control.configure(self.c,{**control.DEFAULTS,**values})
  self.assertFalse((self.h/'state/backup-settings.json').exists())
 def test_daily_time_uses_explicit_timezone(self):
  control.configure(self.c,{**control.DEFAULTS,'time':'03:00'})
  with patch.object(control,'host_timezone',return_value=datetime.timezone(datetime.timedelta(hours=8))):
   self.assertIsNone(control.daily_slot(self.c,datetime.datetime(2026,11,26,18,59,tzinfo=datetime.timezone.utc)))
   self.assertEqual(control.daily_slot(self.c,datetime.datetime(2026,11,26,19,0,tzinfo=datetime.timezone.utc)),'2026-11-27')
 def test_saved_timezone_cannot_override_host(self):
  control.configure(self.c,{**control.DEFAULTS,'timezone':'Pacific/Honolulu'})
  self.assertNotIn('timezone',control.settings(self.c))
  self.assertNotIn('timezone',json.loads((self.h/'state/backup-settings.json').read_text()))
 def test_windows_zone_cache_refreshes_for_offset_changes(self):
  with patch.object(control.os,'uname',return_value=type('U',(),{'release':'microsoft-wsl'})()),patch.object(control,'_HOST_ZONE',{}),patch.object(control.subprocess,'run') as run,patch.object(control.time,'monotonic',return_value=100):
   run.return_value.stdout='{"name":"host-zone","seconds":28800}'
   self.assertEqual(control.host_timezone().utcoffset(None),datetime.timedelta(hours=8))
   control.host_timezone();self.assertEqual(run.call_count,1)
   with patch.object(control.time,'monotonic',return_value=161):
    run.return_value.stdout='{"name":"host-zone","seconds":32400}'
    self.assertEqual(control.host_timezone().utcoffset(None),datetime.timedelta(hours=9))
 def test_daily_retry_never_reuses_older_point_or_undoes_user_cancel(self):
  from nas_continuous import daily_retry_due
  daily={'day':'today','attempted_at':1000,'first_attempt_at':1000}
  point={'state':'success','captured_at':1100,'last_success':'fixture','path':'/fixture'}
  accounts=[{'backup':{'state':'failed'}}]
  self.assertTrue(daily_retry_due(daily,'today',point,accounts,{},1400))
  self.assertFalse(daily_retry_due(daily,'today',{**point,'captured_at':999},accounts,{},1400))
  self.assertFalse(daily_retry_due(daily,'today',point,accounts,{'state':'interrupted'},1400))
  self.assertFalse(daily_retry_due(daily,'today',point,accounts,{},1200))
 def test_cancel_is_job_specific_and_terminal(self):
  with self.assertRaises(control.Interrupted):
   with control.operation(self.c,'cloud') as current:
    with self.assertRaises(RuntimeError):control.cancel(self.c,'other-job')
    control.cancel(self.c,current.row['id']);control.check()
  self.assertEqual(control.status(self.c)['operation']['state'],'interrupted')
  self.assertFalse(list((self.h/'state').glob('backup-cancel-*')))
 def test_restore_cannot_be_cancelled(self):
  with self.assertRaises(control.Interrupted):
   with control.operation(self.c,'capture') as current:
    current.pause();control.cancel(self.c,current.row['id']);current.resume();control.check()
    current.resumed();control.check()
 def test_deadline_triggers_interruption(self):
  with control.operation(self.c,'capture') as current:
   current.pause();current.deadline=time.monotonic()-1
   with self.assertRaisesRegex(control.Interrupted,'最长停写'):control.check()
   current.resume();control.check()
 def test_nested_capture_shares_cloud_job(self):
  with control.operation(self.c,'cloud') as outer:
   with control.operation(self.c,'capture') as inner:self.assertIs(inner,outer)
   self.assertEqual(control.status(self.c)['operation']['state'],'running')
  self.assertEqual(control.status(self.c)['operation']['state'],'success')
 def test_cancellable_child_is_terminated(self):
  with self.assertRaises(control.Interrupted):
   with control.operation(self.c,'cloud') as current:
    timer=threading.Timer(.15,lambda:control.cancel(self.c,current.row['id']));timer.start()
    try:control.run([sys.executable,'-c','import time;time.sleep(30)'],capture_output=True,text=True,check=True)
    finally:timer.join()
 def test_normal_child_output_and_errors_preserved(self):
  with control.operation(self.c,'cloud'):
   row=control.run([sys.executable,'-c','print("fixture")'],capture_output=True,text=True,check=True)
   self.assertEqual(row.stdout,'fixture\n')
   with self.assertRaises(subprocess.CalledProcessError):control.run([sys.executable,'-c','raise SystemExit(7)'],check=True)
 def test_unrelated_application_names_use_attributes(self):
  apps={'arbitrary-writer':{'data_paths':['stuff'],'modules':['writer']},'reader':{'data_paths':['.'],'data_read_only':True,'modules':['reader']},'infra':{'database':'auth','modules':['db','auth'],'backup_writer_modules':['auth']}}
  with patch.object(nas_apps,'APPS',apps):
   self.assertEqual(nas_apps.backup_services({'writer':['writes'],'reader':['reads'],'db':['database'],'auth':['login']}),['writes','login'])
 def test_shared_database_registration_is_extensible(self):
  import nas_database
  with patch.object(nas_apps,'APPS',{'unknown':{'database':'custom_database'},'infra':{'database':'optional','database_activation_config':'optional.env'}}):
   self.assertEqual(nas_database.application_databases(self.c),('custom_database',))

if __name__=='__main__':unittest.main(verbosity=2)
