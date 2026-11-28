#!/usr/bin/env python3
"""Mirrored cursor recovery, identity and explicit metadata budgets, isolated files."""
import sys
sys.dont_write_bytecode=True
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from nas_native_state import State
from nas_stream import IntegrityError
class Tests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);root=Path(self.temp.name);(root/'state').mkdir();(root/'snapshots').mkdir();snapshots=Mock();snapshots.root=root/'snapshots';self.s=State({'host':str(root),'data_storage':{'uuid':'fixture-volume'}},snapshots)
 def test_cursor_recovers_highest_committed_revision(self):
  self.s.account('target',data_sequence=1);old=self.s.file.read_bytes();self.s.account('target',data_sequence=2);self.s.file.write_bytes(old);self.assertEqual(self.s.snapshot()['accounts']['target']['data_sequence'],2)
 def test_one_corrupt_copy_recovers_but_both_corrupt_fail_closed(self):
  self.s.account('target',data_sequence=1);self.s.file.write_text('{');self.assertEqual(self.s.snapshot()['accounts']['target']['data_sequence'],1);self.s.mirror.write_text('{')
  with self.assertRaises(IntegrityError):self.s.snapshot()
 def test_foreign_volume_cannot_be_adopted(self):
  self.s.account('target',data_sequence=1);value=json.loads(self.s.file.read_text());value['filesystem']='foreign';self.s.file.write_text(json.dumps(value))
  with self.assertRaises(IntegrityError):self.s.snapshot()
 def test_linked_copy_refused(self):
  self.s.account('target',data_sequence=1);self.s.file.unlink();self.s.file.symlink_to(self.s.mirror)
  with self.assertRaises(IntegrityError):self.s.snapshot()
 def test_budget_failure_does_not_overwrite_last_cursor(self):
  self.s.account('target',data_sequence=1)
  with self.assertRaises(IntegrityError):self.s.account('target',oversized='a'*(2*1024**2))
  self.assertEqual(self.s.snapshot()['accounts']['target']['data_sequence'],1)
if __name__=='__main__':unittest.main()
