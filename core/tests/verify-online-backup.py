#!/usr/bin/env python3
"""No-copy online capture, cancellation and truthful consistency contracts."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import contextlib,datetime,json,tempfile,types,unittest
from unittest.mock import patch
import nas_online as online,nas_backup_control as control,nas_cloud as cloud,nasctl,nas_database,nas_backup_config

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.h=Path(self.tmp.name)
  for name in ('state','config','runtime','secrets','data'):(self.h/name).mkdir()
  self.c={'host':str(self.h),'data':str(self.h/'data'),'project':'fixture','backup_root':str(self.h/'old-backups')}
  self.original=self.h/'data/original.bin';self.original.write_bytes(b'unchanged'*1024*256)
  self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
  def mock(obj,key,**kw):return self.stack.enter_context(patch.object(obj,key,**kw))
  mock(nasctl,'volume');mock(nasctl,'verify_mount');self.compose=mock(nasctl,'compose')
  mock(nasctl,'inventory',return_value=[{'service':'database','state':'running','health':'healthy'}])
  mock(nas_database,'application_databases',return_value=('alpha','beta'));mock(nas_database,'primary_database',return_value='alpha')
  mock(nas_backup_config,'archive_config',side_effect=lambda c,path:path.write_bytes(b'configuration-fixture'))
  def command(args,**kw):
   if kw.get('stdout'):kw['stdout'].write(b'export-fixture')
   return types.SimpleNamespace(returncode=0,stdout='0')
  self.run=mock(online,'run',side_effect=command)
 def test_bundle_contains_only_metadata_and_never_copies_or_stops(self):
  before=self.original.stat()
  with control.operation(self.c,'cloud'):
   with online.bundle(self.c) as stage:
    manifest=online.validate(self.c,stage)
    self.assertEqual(manifest['consistency'],'eventual');self.assertEqual(set(manifest['databases']),{'alpha','beta'})
    self.assertEqual(list((stage/'data').iterdir()),[])
    self.assertLess(sum(p.stat().st_size for p in stage.iterdir() if p.is_file()),100000)
    self.assertFalse((stage/'user-content.tar').exists());self.assertTrue((stage/'database.dump').exists())
   self.assertFalse(stage.exists())
  state=json.loads((self.h/'state/cloud-online.json').read_text())
  self.assertEqual(state['data_bytes_copied'],0);self.assertLess(state['metadata_bytes'],100000)
  self.assertEqual(self.original.stat().st_ino,before.st_ino);self.assertEqual(self.original.stat().st_size,before.st_size);self.compose.assert_not_called()
 def test_cancelled_exports_remove_metadata_without_touching_originals(self):
  original=self.run.side_effect
  def cancel_export(args,**kwargs):
   if 'pg_dump' in args:
    kwargs['stdout'].write(b'partial-export');raise control.Interrupted('cancel')
   return original(args,**kwargs)
  self.run.side_effect=cancel_export
  with self.assertRaises(control.Interrupted):
   with control.operation(self.c,'cloud'):
    with online.bundle(self.c):pass
  self.assertEqual(list((self.h/'runtime').iterdir()),[]);self.assertTrue(self.original.exists());self.compose.assert_not_called()
 def test_corrupt_export_is_rejected(self):
  with online.bundle(self.c) as stage:
   (stage/'database.dump').write_bytes(b'corrupt')
   with self.assertRaisesRegex(RuntimeError,'checksum'):online.validate(self.c,stage)
 def test_source_not_ready_is_not_silently_a_files_only_success(self):
  with patch.object(nasctl,'inventory',return_value=[]):
   with self.assertRaisesRegex(RuntimeError,'数据库'):
    with online.bundle(self.c):pass
  self.assertEqual(list((self.h/'runtime').iterdir()),[])
 def test_worker_has_readonly_live_child_mount(self):
  cloud.save(self.h/'config/versions.json',{'CLOUD_BACKUP_IMAGE':{'image':'fixture'}})
  with patch.object(cloud,'backup_run',return_value=types.SimpleNamespace(returncode=0,stdout='',stderr='')) as run:
   cloud.command(self.c,['restic','backup','/backup-set'],source=self.h/'runtime',live_data=self.c['data'])
  args=run.call_args.args[0]
  self.assertIn('type=bind,source='+self.c['data']+',target=/backup-set/data,readonly',args)
  self.assertNotIn('/var/run/docker.sock',str(args[args.index('run')+1:]))
 def test_online_loop_is_bounded_and_stopping_delays_retry(self):
  from nas_continuous import online_due
  self.assertFalse(online_due({}, {'state':'running'},1000))
  self.assertFalse(online_due({'next_at':1100},{},1000))
  stamp=datetime.datetime.fromtimestamp(1000,datetime.timezone.utc).isoformat()
  self.assertFalse(online_due({}, {'state':'success','finished':stamp},1299))
  self.assertTrue(online_due({}, {'state':'success','finished':stamp},1300))
  self.assertFalse(online_due({}, {'state':'interrupted','finished':stamp},2799))
  self.assertTrue(online_due({}, {'state':'interrupted','finished':stamp},2800))

if __name__=='__main__':unittest.main(verbosity=2)
