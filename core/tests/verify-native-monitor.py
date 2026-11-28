#!/usr/bin/env python3
"""Ownership-preserving reclamation with no physical disk or cloud side effects."""
import sys,unittest,copy
from pathlib import Path
from unittest.mock import Mock
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from nas_native_monitor import release
class State:
 def __init__(self,value):self.value=copy.deepcopy(value)
 def snapshot(self):return copy.deepcopy(self.value)
 def update(self,fn):fn(self.value)
class Monitor(unittest.TestCase):
 def manager(self):
  m=Mock();m.state=State({'drop_requested':True,'checkpoints':{'a':{'state':'ready','snapshot_uuid':'uuid-a'}},'latest':{'id':'a'},'accounts':{'target':{'data_parent':'a','data_pending':{'id':'pending','identity':{'source':'stable'}}}}});m.snapshots.path.return_value.exists.return_value=True;return m
 def test_only_registered_uuid_removed_and_remote_intent_kept(self):
  m=self.manager();release(m);m.snapshots.discard.assert_called_once_with('a','uuid-a');s=m.state.snapshot();self.assertFalse(s['drop_requested']);self.assertIsNone(s['latest']);self.assertEqual(s['checkpoints'],{});self.assertIsNone(s['accounts']['target']['data_parent']);self.assertEqual(s['accounts']['target']['data_pending']['id'],'pending')
 def test_failed_delete_keeps_registration(self):
  m=self.manager();m.snapshots.discard.side_effect=RuntimeError('fixture disconnect')
  with self.assertRaises(RuntimeError):release(m)
  self.assertIn('a',m.state.snapshot()['checkpoints']);self.assertTrue(m.state.snapshot()['drop_requested'])
 def test_crash_after_delete_is_resumable(self):
  m=self.manager();m.snapshots.path.return_value.exists.return_value=False;release(m);m.snapshots.discard.assert_not_called();self.assertFalse(m.state.snapshot()['drop_requested'])
 def test_unregistered_uuid_cannot_be_guessed(self):
  m=self.manager();m.state.value['checkpoints']['a']={'state':'creating'}
  with self.assertRaises(RuntimeError):release(m)
  m.snapshots.discard.assert_not_called()
 def test_requires_durable_reclamation_intent(self):
  m=self.manager();m.state.value['drop_requested']=False
  with self.assertRaises(RuntimeError):release(m)
  m.snapshots.discard.assert_not_called()
if __name__=='__main__':unittest.main()
