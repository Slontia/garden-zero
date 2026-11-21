#!/usr/bin/env python3
"""Mocked lifecycle wiring and retry policy; never pauses real services."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import contextlib,unittest
from unittest.mock import patch
import nasctl
from nas_policy import mirror_due
class Tests(unittest.TestCase):
 def setUp(self):self.c={'host':'/fixture','hdd_root':'/fixture-target'}
 def test_fresh_snapshot_precedes_mirror(self):
  calls=[]
  with patch.object(nasctl,'backup',side_effect=lambda c:calls.append('snapshot') or {'state':'success'}),patch.object(nasctl,'run',side_effect=lambda args:calls.append('mirror')):
   nasctl.backup_with_mirror(self.c)
  self.assertEqual(calls,['snapshot','mirror'])
 def test_stopped_database_never_mirrors_stale_data(self):
  with patch.object(nasctl,'backup',return_value={'state':'skipped'}),patch.object(nasctl,'run') as run:
   nasctl.backup_with_mirror(self.c);run.assert_not_called()
 def test_no_target_keeps_local_backup(self):
  with patch.object(nasctl,'backup',return_value={'state':'success'}),patch.object(nasctl,'run') as run:
   nasctl.backup_with_mirror({'hdd_root':None});run.assert_not_called()
 def test_primary_backup_action_uses_mirror(self):
  with patch.object(nasctl,'lock',contextlib.nullcontext),patch.object(nasctl,'backup_with_mirror') as backup:
   nasctl.action(self.c,{'action':'backup'});backup.assert_called_once_with(self.c,purge_history=False)
 def test_hdd_button_refreshes_source(self):
  with patch.object(nasctl,'lock',contextlib.nullcontext),patch.object(nasctl,'backup_with_mirror',return_value={'state':'success'}) as backup:
   nasctl.action(self.c,{'action':'hdd-backup'});backup.assert_called_once_with(self.c,purge_history=False)
 def test_retry_requires_complete_local_snapshot(self):
  valid={'state':'success','last_success':'20260101-030000'}
  self.assertTrue(mirror_due(self.c,valid,{}))
  self.assertTrue(mirror_due(self.c,valid,{'state':'failed','source':valid['last_success']}))
  self.assertFalse(mirror_due(self.c,valid,{'state':'success','source':valid['last_success']}))
  for state in ['failed','skipped','running']:
   self.assertFalse(mirror_due(self.c,{**valid,'state':state},{}))
  self.assertFalse(mirror_due(self.c,{'state':'success','last_success':'invalid'},{}))
  self.assertFalse(mirror_due({'hdd_root':None},valid,{}))
 def test_explicit_history_cleanup_flag_reaches_adapter(self):
  with patch.object(nasctl,'backup',return_value={'state':'success'}),patch.object(nasctl,'run') as run:
   nasctl.backup_with_mirror(self.c,purge_history=True);self.assertIn('--purge-history',run.call_args.args[0])
 def test_ui_cleanup_choice_reaches_backup(self):
  with patch.object(nasctl,'lock',contextlib.nullcontext),patch.object(nasctl,'backup_with_mirror') as backup:
   nasctl.action(self.c,{'action':'backup','purge_history':True});backup.assert_called_once_with(self.c,purge_history=True)
 def test_invalid_cleanup_choice_rejected_before_backup(self):
  with patch.object(nasctl,'backup_with_mirror') as backup:
   with self.assertRaises(RuntimeError):nasctl.action(self.c,{'action':'backup','purge_history':'true'})
   backup.assert_not_called()
if __name__=='__main__':unittest.main(verbosity=2)
