#!/usr/bin/env python3
"""Transient-unit limits, launch handoff and failure cleanup without systemd writes."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import tempfile,unittest,json,subprocess
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import nas_native_jobs as jobs
from nas_backup_control import save,begin
class Tests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);(self.root/'state').mkdir();self.c={'host':str(self.root),'data':'/fixture/data'};self.row=begin(self.c,'cloud')
 def test_transient_worker_is_limited_and_retains_cancellation_id(self):
  calls=[]
  def run(args,**kw):
   calls.append(args)
   if args[0]=='systemd-run':save(self.root/'state/backup-operation.json',{**self.row,'pid':self.row['pid']+1})
   return subprocess.CompletedProcess(args,0,'','')
  with patch('nas_storage.specification',return_value=({'mount':'/fixture'},None)),patch('nas_storage.device',return_value=Path('/dev/fixture2')),patch.object(jobs.subprocess,'run',side_effect=run),patch('nas_backup_control.alive',return_value=True):result=jobs.launch(self.c,[{'id':'cloud-fixture'}],self.row)
  self.assertEqual(result['task'],self.row['id']);args=calls[0]
  for flag in ['--property=CPUQuota=20%','--property=MemoryMax=512M','--property=MemorySwapMax=0','--property=ProtectSystem=strict','--property=ReadOnlyPaths=/fixture/data']:self.assertIn(flag,args)
  self.assertTrue(any(x.endswith(' 4194304') for x in args));self.assertTrue(any(x.endswith(' 1048576') for x in args));self.assertTrue(any('/run/nas/lifecycle.lock' in x for x in args))
 def test_failed_launch_closes_operation_and_removes_only_own_spec(self):
  def run(args,**kw):
   if args[0]=='systemd-run':raise subprocess.CalledProcessError(1,args)
   return subprocess.CompletedProcess(args,0,'','')
  with patch('nas_storage.specification',return_value=({'mount':'/fixture'},None)),patch('nas_storage.device',return_value=Path('/dev/fixture2')),patch.object(jobs.subprocess,'run',side_effect=run):
   with self.assertRaisesRegex(RuntimeError,'could not start'):jobs.launch(self.c,[{'id':'cloud-fixture'}],self.row)
  self.assertEqual(json.loads((self.root/'state/backup-operation.json').read_text())['state'],'failed');self.assertFalse((self.root/'state/native-jobs'/(self.row['id']+'.json')).exists())
if __name__=='__main__':unittest.main()
