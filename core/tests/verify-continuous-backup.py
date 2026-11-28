#!/usr/bin/env python3
"""Isolated incremental capture and scheduling tests; no real services or accounts."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import hashlib,json,os,tempfile,unittest
from unittest.mock import patch
import nas_continuous as live

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.h=Path(self.tmp.name)
  for name in ['data','backups','state']:(self.h/name).mkdir()
  self.c={'host':str(self.h),'data':str(self.h/'data'),'backup_root':str(self.h/'backups')}
  self.root=live.roots(self.c)
 def builder(self,stamp):
  stage=self.root/('.incomplete-'+stamp);stage.mkdir();return live.Builder(self.c,stage)
 def commit(self,b,stamp):
  b.prepare(final=True)
  for name in ['database.dump','configuration.tar']:(b.stage/name).write_bytes(name.encode())
  m={'schema':4,'time':stamp,'databases':{'immich':{'dump':'database.dump'}},'content':b.entries,'files':{n:live.digest_file(b.stage/n) for n in ['database.dump','configuration.tar']}}
  (b.stage/'manifest.json').write_text(json.dumps(m));(b.stage/'COMPLETE').write_text(stamp+'\n')
  dest=self.root/stamp;b.stage.rename(dest)
  (self.h/'state/cloud-snapshot.json').write_text(json.dumps({'state':'success','path':str(dest)}));return dest
 def test_incremental_reuses_unchanged_files_without_rewriting_previous_generation(self):
  (self.h/'data/a').write_text('original');first=self.commit(self.builder('20261126-000001'),'20261126-000001')
  b=self.builder('20261126-000002');b.prepare(final=True)
  self.assertEqual(b.copied,0);self.assertEqual(b.reused,1)
  (self.h/'data/a').write_text('modified');b.prepare(final=True)
  self.assertEqual((first/'data/a').read_text(),'original');self.assertEqual((b.data/'a').read_text(),'modified')
 def test_delete_is_removed_from_new_view_but_retained_in_previous(self):
  (self.h/'data/removed').write_text('keep history');first=self.commit(self.builder('20261126-000001'),'20261126-000001')
  (self.h/'data/removed').unlink();second=self.commit(self.builder('20261126-000002'),'20261126-000002')
  self.assertFalse((second/'data/removed').exists());self.assertTrue((first/'data/removed').exists())
 def test_interrupted_new_capture_retains_previous_complete_copy(self):
  (self.h/'data/a').write_text('prior');first=self.commit(self.builder('20261126-000001'),'20261126-000001')
  (self.h/'data/a').write_text('next');b=self.builder('20261126-000002');b.prepare()
  self.assertFalse((b.stage/'COMPLETE').exists());live.validate(self.c,first)
 def test_manifest_checks_content_corruption(self):
  (self.h/'data/a').write_text('data');first=self.commit(self.builder('20261126-000001'),'20261126-000001')
  (first/'data/a').write_text('evil')
  with self.assertRaisesRegex(RuntimeError,'checksum'):live.validate(self.c,first)
 def test_corrupted_previous_generation_is_not_reused(self):
  (self.h/'data/a').write_text('data');first=self.commit(self.builder('20261126-000001'),'20261126-000001')
  (first/'data/a').write_text('evil');b=self.builder('20261126-000002')
  with self.assertRaisesRegex(RuntimeError,'Previous'):b.prepare()
 def test_live_symlink_outside_data_is_rejected(self):
  (self.h/'private').write_text('secret');(self.h/'data/link').symlink_to(self.h/'private')
  with self.assertRaisesRegex(RuntimeError,'link'):self.builder('20261126-000001').prepare()
 def test_extra_or_missing_file_invalidates_recovery_point(self):
  (self.h/'data/a').write_text('a');first=self.commit(self.builder('20261126-000001'),'20261126-000001')
  (first/'data/unknown').write_text('unknown')
  with self.assertRaisesRegex(RuntimeError,'manifest'):live.validate(self.c,first)
 def test_incomplete_source_not_accepted(self):
  b=self.builder('20261126-000001')
  with self.assertRaises(RuntimeError):live.validate(self.c,b.stage)
 def test_database_and_configuration_required(self):
  first=self.commit(self.builder('20261126-000001'),'20261126-000001');(first/'database.dump').unlink()
  with self.assertRaises((RuntimeError,OSError)):live.validate(self.c,first)
 def test_pre_copy_is_refreshed_after_files_change(self):
  (self.h/'data/a').write_text('old');b=self.builder('20261126-000001');b.prepare()
  (self.h/'data/a').write_text('new');(self.h/'data/b').write_text('added');b.prepare(final=True)
  self.assertEqual((b.data/'a').read_text(),'new');self.assertEqual(set(b.entries),{'a','b'})
 def test_changing_source_is_not_committed(self):
  (self.h/'data/a').write_text('file');b=self.builder('20261126-000001');original=live.inventory
  calls=0
  def unstable(root):
   nonlocal calls
   result=original(root);calls+=1
   if calls==2:result['a'][1]+=1
   return result
  with patch.object(live,'inventory',side_effect=unstable):
   with self.assertRaisesRegex(RuntimeError,'changed'):b.prepare(final=True)
  self.assertFalse((b.stage/'COMPLETE').exists())
 def test_no_pause_per_photo_changes_are_batched(self):
  c=live.Changes();c.last_attempt=1000;c.observe('first',1001)
  self.assertFalse(c.due(1032,1000));self.assertTrue(c.due(1300,1000))
 def test_continuous_writes_have_max_delay(self):
  c=live.Changes();c.observe('a',1000);c.observe('b',1299)
  self.assertTrue(c.due(1300,1000))
 def test_upload_time_changes_are_not_acknowledged_by_old_capture(self):
  c=live.Changes();c.observe('captured',1000);c.observe('new-upload',1010);c.acknowledge('captured')
  self.assertIsNotNone(c.first)
 def test_remount_device_number_does_not_force_a_second_full_copy(self):
  (self.h/'data/a').write_text('same');first=self.commit(self.builder('20261126-000001'),'20261126-000001')
  m=json.loads((first/'manifest.json').read_text());m['content']['a']['source_stat'][-1]+=1;(first/'manifest.json').write_text(json.dumps(m))
  b=self.builder('20261126-000002');b.prepare(final=True)
  self.assertEqual(b.copied,0);self.assertEqual(b.reused,1)
 def test_immich_startup_marker_touches_do_not_create_backup_restart_loop(self):
  for name in ['config','secrets']:(self.h/name).mkdir()
  folder=self.h/'data/immich/upload';folder.mkdir(parents=True);marker=folder/'.immich';marker.write_text('{"version":1}')
  before=live.fingerprint(self.c)
  marker.write_text('{"version":1}');st=marker.stat();os.utime(marker,ns=(st.st_atime_ns,st.st_mtime_ns+1000000000))
  self.assertEqual(before,live.fingerprint(self.c))
  marker.write_text('{"version":2}');self.assertEqual(before,live.fingerprint(self.c))
  (folder/'photo.jpg').write_bytes(b'new photo');self.assertNotEqual(before,live.fingerprint(self.c))
 def test_directory_timestamp_touch_does_not_hide_real_file_change(self):
  for name in ['config','secrets']:(self.h/name).mkdir()
  folder=self.h/'data/folder';folder.mkdir();(folder/'a').write_text('old')
  before=live.fingerprint(self.c);st=folder.stat();os.utime(folder,ns=(st.st_atime_ns,st.st_mtime_ns+1000000000))
  self.assertEqual(before,live.fingerprint(self.c))
  (folder/'a').write_text('new');self.assertNotEqual(before,live.fingerprint(self.c))
 def test_failed_upload_retries_completed_point_before_capturing_new_changes(self):
  state={'state':'success','last_success':'20261126-000001','path':'/fixture/complete','fingerprint':'older-data'}
  self.assertTrue(live.pending_recovery_point(state,[{'backup':{'state':'failed'}}]))
  self.assertFalse(live.pending_recovery_point(state,[{'backup':{'state':'success','source':'20261126-000001'}}]))
  self.assertFalse(live.pending_recovery_point({},[{'backup':{'state':'failed'}}]))
 def test_periodic_checkpoint_covers_database_only_changes(self):
  c=live.Changes();self.assertTrue(c.due(4600,1000));self.assertFalse(c.due(4599,1000))
 def test_fast_validation_reuses_verified_unchanged_content_hashes(self):
  (self.h/'data/a').write_text('same');first=self.commit(self.builder('20261126-000001'),'20261126-000001')
  second=self.commit(self.builder('20261126-000002'),'20261126-000002')
  with patch.object(live,'digest_file',wraps=live.digest_file) as digest:live.validate(self.c,second,full=False)
  self.assertNotIn(second/'data/a',[call.args[0] for call in digest.call_args_list])
  with patch.object(live,'digest_file',wraps=live.digest_file) as digest:live.validate(self.c,second,full=True)
  self.assertIn(second/'data/a',[call.args[0] for call in digest.call_args_list])
 def test_fast_validation_still_hashes_new_or_changed_content(self):
  (self.h/'data/a').write_text('new');first=self.commit(self.builder('20261126-000001'),'20261126-000001')
  with patch.object(live,'digest_file',wraps=live.digest_file) as digest:live.validate(self.c,first,full=False)
  self.assertIn(first/'data/a',[call.args[0] for call in digest.call_args_list])
 def test_copy_preserves_configured_database_disk_reserve(self):
  import types
  self.c['critical_image_outer_free']=8*1024**3;(self.h/'data/a').write_text('data')
  b=self.builder('20261126-000001')
  with patch.object(live.shutil,'disk_usage',return_value=types.SimpleNamespace(free=4*1024**3)):
   with self.assertRaisesRegex(RuntimeError,'Insufficient'):b.prepare()
  self.assertFalse((b.stage/'COMPLETE').exists())
 def test_executable_permission_is_preserved(self):
  source=self.h/'data/hook';source.write_text('#!/bin/sh');source.chmod(0o755)
  b=self.builder('20261126-000001');b.prepare(final=True)
  self.assertEqual((b.data/'hook').stat().st_mode&0o777,0o755)
 def test_long_file_names_do_not_overflow_temporary_copy_names(self):
  name='a'*253;(self.h/'data'/name).write_text('long name')
  b=self.builder('20261126-000001');b.prepare(final=True)
  self.assertEqual((b.data/name).read_text(),'long name')
 def test_bounded_parallel_metadata_scan_matches_serial_results(self):
  (self.h/'data/folder').mkdir()
  for i in range(70):(self.h/'data/folder'/str(i)).write_text(str(i))
  self.assertEqual(live.inventory(self.h/'data',workers=1),live.inventory(self.h/'data',workers=4))
 def test_case_colliding_paths_are_rejected_before_capture(self):
  (self.h/'data/A').write_text('one');(self.h/'data/a').write_text('two')
  with self.assertRaisesRegex(RuntimeError,'Case-colliding'):self.builder('20261126-000001').prepare()
 def test_type_changes_and_empty_directories(self):
  (self.h/'data/a').write_text('old');b=self.builder('20261126-000001');b.prepare()
  (self.h/'data/a').unlink();(self.h/'data/a').mkdir();(self.h/'data/empty').mkdir();b.prepare(final=True)
  self.assertTrue((b.data/'a').is_dir());self.assertTrue((b.data/'empty').is_dir());self.assertEqual(b.entries,{})

 def test_retention_only_removes_confirmed_unpinned_old_working_generations(self):
  import nas_cloud
  paths=[]
  for i in range(1,4):
   stamp='20261126-00000'+str(i);(self.h/'data/a').write_text(str(i));paths.append(self.commit(self.builder(stamp),stamp))
  (self.h/'state/cloud-local-confirmed.json').write_text(json.dumps({'sources':[p.name for p in paths]}))
  with patch.object(nas_cloud,'metadata',return_value={'accounts':[{'backup':{'source':paths[0].name}}]}):live.confirmed(self.c,paths[-1])
  self.assertTrue(paths[0].exists())
  with patch.object(nas_cloud,'metadata',return_value={'accounts':[]}):live.confirmed(self.c,paths[-1])
  self.assertFalse(paths[0].exists());self.assertTrue(paths[1].exists());self.assertTrue(paths[2].exists())
 def test_unconfirmed_and_incomplete_local_copies_are_not_collected(self):
  import nas_cloud
  paths=[]
  for i in range(1,4):
   stamp='20261126-00000'+str(i);paths.append(self.commit(self.builder(stamp),stamp))
  unfinished=self.builder('20261126-000004')
  with patch.object(nas_cloud,'metadata',return_value={'accounts':[]}):live.confirmed(self.c,paths[-1])
  self.assertTrue(paths[0].exists());self.assertTrue(unfinished.stage.exists())

class LifecycleTests(unittest.TestCase):
 def setUp(self):
  import contextlib,types
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.h=Path(self.tmp.name)
  self.c={'host':str(self.h/'system'),'root':str(self.h/'nas'),'data':str(self.h/'data'),'backup_root':str(self.h/'backups'),'project':'fixture','image_uuid':'fixture-image'}
  for directory in ['backups','data','system/config','system/secrets','system/state/git','system/state/tailscale','system/state/syncthing','system/logs']:(self.h/directory).mkdir(parents=True,exist_ok=True)
  for directory in ['web','identity','scripts','tests','services','templates','host','docs','compose']:(self.h/'nas/core'/directory).mkdir(parents=True,exist_ok=True)
  for name in ['README.md','deploy.sh','start.sh','stop.sh','start.ps1','stop.ps1','.env.example','.gitignore','.gitattributes']:(self.h/'nas/core'/name).write_text('fixture')
  for name in ['config.xml','cert.pem','key.pem','https-cert.pem','https-key.pem']:(self.h/'system/state/syncthing'/name).write_text('fixture')
  (self.h/'data/photo').write_text('photo')
  import nasctl as n,nas_database as db
  self.n=n;self.rows={'database':{'service':'database','state':'running','exit_code':0},'immich-server':{'service':'immich-server','state':'running','exit_code':0}}
  self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
  def mock(obj,key,**kw):return self.stack.enter_context(patch.object(obj,key,**kw))
  mock(n,'volume');mock(n,'verify_mount');mock(n,'audit');mock(n,'output',return_value='0');mock(n,'db_signature',return_value={});mock(n,'inventory',side_effect=lambda c:list(self.rows.values()));mock(n,'WRITER',new=types.SimpleNamespace(revoke=lambda _:None))
  mock(db,'application_databases',return_value=['immich','joplin','platform_auth'])
  mock(live,'fingerprint',return_value='captured-fingerprint')
  mock(n.shutil,'disk_usage',return_value=types.SimpleNamespace(free=100*1024**3))
  def command(args,**kw):
   if kw.get('stdout'):kw['stdout'].write(b'fixture-database-export')
   return types.SimpleNamespace(returncode=0)
  import nas_backup_control
  mock(nas_backup_control,'run',side_effect=command)
  def compose(c,action,*args,**kw):
   for name,row in self.rows.items():
    if name in args:row['state']='exited' if action=='stop' else 'running'
  self.compose=mock(n,'compose',side_effect=compose)
 def test_full_capture_exports_all_databases_without_content_tar_and_resumes_writers(self):
  result=self.n.backup(self.c,incremental=True)
  self.assertEqual(result['state'],'success');self.assertEqual(self.rows['immich-server']['state'],'running')
  path=Path(result['path']);self.assertFalse((path/'user-content.tar').exists())
  self.assertEqual((path/'data/photo').read_text(),'photo')
  manifest=live.validate(self.c,path);self.assertEqual(set(manifest['databases']),{'immich','joplin','platform_auth'})
  self.assertTrue((path/'database-roles.sql').is_file());self.assertTrue((path/'configuration.tar').is_file())
 def test_database_export_budget_is_reserved_before_stopping_writers(self):
  import types
  self.c['critical_image_outer_free']=16*1024**3
  with patch.object(self.n,'output',return_value=str(2*1024**3)),patch.object(self.n.shutil,'disk_usage',return_value=types.SimpleNamespace(free=19*1024**3)):
   with self.assertRaisesRegex(RuntimeError,'reserve'):self.n.backup(self.c,incremental=True)
  self.compose.assert_not_called();self.assertEqual(self.rows['immich-server']['state'],'running')
 def test_capture_failure_resumes_writers_and_never_commits(self):
  original=live.Builder.prepare
  def fail(builder,final=False):
   if final:raise RuntimeError('file changed')
   return original(builder,final)
  with patch.object(live.Builder,'prepare',new=fail):
   with self.assertRaisesRegex(RuntimeError,'file changed'):self.n.backup(self.c,incremental=True)
  self.assertEqual(self.rows['immich-server']['state'],'running')
  self.assertFalse(list((self.h/'backups/cloud').glob('*/COMPLETE')))
 def test_partial_stop_failure_still_attempts_to_resume_original_writer(self):
  def partial(c,action,*args,**kw):
   if action=='stop':self.rows['immich-server']['state']='exited';raise RuntimeError('partial stop failure')
   self.rows['immich-server']['state']='running'
  self.compose.side_effect=partial
  with self.assertRaisesRegex(RuntimeError,'partial stop failure'):self.n.backup(self.c,incremental=True)
  self.assertEqual(self.rows['immich-server']['state'],'running')
 def test_user_cancel_after_stop_restores_writers_without_committing(self):
  import nas_backup_control as control
  original=self.compose.side_effect
  def cancel_after_stop(c,action,*args,**kw):
   original(c,action,*args,**kw)
   if action=='stop':control.cancel(c,control.status(c)['operation']['id'])
  self.compose.side_effect=cancel_after_stop
  with self.assertRaises(control.Interrupted):self.n.backup(self.c,incremental=True)
  self.assertEqual(self.rows['immich-server']['state'],'running')
  self.assertEqual(control.status(self.c)['operation']['state'],'interrupted')
  self.assertFalse(list((self.h/'backups/cloud').glob('*/COMPLETE')))
 def test_pause_timeout_restores_writers_without_committing(self):
  import nas_backup_control as control
  original=self.compose.side_effect
  def timeout_after_stop(c,action,*args,**kw):
   original(c,action,*args,**kw)
   if action=='stop':control.ACTIVE.get().deadline=0
  self.compose.side_effect=timeout_after_stop
  with self.assertRaisesRegex(control.Interrupted,'最长停写'):self.n.backup(self.c,incremental=True)
  self.assertEqual(self.rows['immich-server']['state'],'running')
  self.assertFalse(list((self.h/'backups/cloud').glob('*/COMPLETE')))

if __name__=='__main__':unittest.main(verbosity=2)
