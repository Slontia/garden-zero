#!/usr/bin/env python3
"""Unified records, legacy evidence and script-only disk actions; no real storage."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import datetime,json,tempfile,unittest
from unittest.mock import patch
import nas_backup_history as history
import nas_backup_control as control
import nasctl

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.h=Path(self.tmp.name)
  (self.h/'state').mkdir();(self.h/'logs').mkdir();self.c={'host':str(self.h),'hdd_root':'/mnt/f/nas-backup','hdd_guid':'disk-fixture'}
  self.account={'id':'cloud-fixture','name':'Fixture cloud','provider':'aliyun','refresh_token':'must-never-appear'}
 def test_each_target_has_one_durable_row_per_job(self):
  with control.operation(self.c,'cloud'):
   key=history.cloud_record(self.c,self.account)
   history.cloud_record(self.c,self.account,{'state':'success','started':'2099-01-01','finished':'2026-11-27T01:00:00+08:00','snapshot_id':'fixture','refresh_token':'must-never-appear'},key)
  rows=history.unified(self.c);self.assertEqual(len(rows),1);self.assertEqual(rows[0]['state'],'success')
  self.assertNotEqual(rows[0]['started'],'2099-01-01');self.assertNotIn('must-never-appear',json.dumps(rows))
  self.assertNotIn('pid',rows[0])
 def test_disk_and_cloud_history_share_one_list(self):
  row={'operation':'backup','state':'success','started':'2026-11-26T00:00:00+00:00'}
  path=history.begin(self.c,row);history.finish(self.c,path,row)
  history.cloud_record(self.c,self.account,{'state':'failed','started':'2026-11-27T00:00:00+00:00','error':'fixture'})
  rows=history.unified(self.c);self.assertEqual([r['type'] for r in rows],['cloud','disk'])
  self.assertEqual(rows[1]['target']['uid'],'disk-fixture');self.assertEqual(rows[1]['target']['relative_path'],'nas-backup')
 def test_removed_account_keeps_target_and_result(self):
  history.cloud_record(self.c,self.account,{'state':'interrupted','error':'cancelled'})
  (self.h/'state/cloud-accounts.json').write_text('{"accounts":[]}')
  row=history.unified(self.c)[0];self.assertEqual(row['target']['name'],'Fixture cloud');self.assertEqual(row['state'],'interrupted')
 def test_old_events_restore_only_recorded_target_results(self):
  events=[{'time':'2026-11-26T00:00:00Z','operation':'backup-start','account':'old','source':'point'},
   {'time':'2026-11-26T00:01:00Z','operation':'backup-success','account':'old','snapshot_id':'old-snapshot'},
   {'time':'2026-11-26T00:02:00Z','operation':'backup-job-failed','account':None}]
  (self.h/'logs/cloud-events.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
  rows=history.unified(self.c);self.assertEqual(len(rows),1);self.assertEqual(rows[0]['source'],'point');self.assertTrue(rows[0]['legacy'])
 def test_new_event_and_journal_are_not_duplicated(self):
  key=history.cloud_record(self.c,self.account,{'state':'success'})
  events=[{'operation':'backup-start','account':self.account['id'],'history_id':key}, {'operation':'backup-success','account':self.account['id']}]
  (self.h/'logs/cloud-events.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
  self.assertEqual(len(history.unified(self.c)),1)
 def test_atomic_updates_invalidate_record_cache(self):
  with control.operation(self.c,'cloud'):
   key=history.cloud_record(self.c,self.account);self.assertEqual(history.unified(self.c)[0]['state'],'running')
   history.cloud_record(self.c,self.account,{'state':'failed'},key);self.assertEqual(history.unified(self.c)[0]['state'],'failed')
 def test_dead_process_is_interrupted_not_still_running(self):
  key=history.cloud_record(self.c,self.account)
  path=self.h/'state/cloud-backup-history'/(key+'.json');row=json.loads(path.read_text());row['pid']=999999999;history.atomic(path,row)
  self.assertEqual(history.unified(self.c)[0]['state'],'interrupted')
 def test_web_disk_actions_rejected_before_any_backup(self):
  with patch.object(nasctl,'backup_with_mirror') as backup:
   for action in ('backup','hdd-backup'):
    with self.assertRaisesRegex(RuntimeError,'脚本'):nasctl.action(self.c,{'action':action})
   backup.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
