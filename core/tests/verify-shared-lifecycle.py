#!/usr/bin/env python3
"""Shared dependency lifecycle and credential safety, isolated from production."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import contextlib,json,tempfile,unittest
from unittest.mock import patch
import nasctl as n
import nas_database as db

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name)
  for name in ['state','config','secrets']:(root/name).mkdir()
  self.c={'host':str(root),'project':'fixture','ml_enabled':False,'autostart':['meta','tailscale'],'root':str(root),'data':str(root/'data')}
  self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
 def start(self,module):
  for name in ['mount','volume','wait_services','audit']:self.stack.enter_context(patch.object(n,name))
  self.stack.enter_context(patch.object(n,'database_lock',contextlib.nullcontext));self.stack.enter_context(patch('nas_database.provision'));self.stack.enter_context(patch('nas_joplin.prepare'))
  self.stack.enter_context(patch.object(n,'output',return_value='{"services":{}}'))
  compose=self.stack.enter_context(patch.object(n,'compose'));n.start(self.c,module);return [x.args[1:] for x in compose.call_args_list]
 def test_joplin_starts_shared_database_before_application(self):
  calls=self.start('knowledge');self.assertEqual(calls[0][-1],'database');self.assertEqual(calls[-1][-1],'joplin')
 def test_immich_does_not_own_shared_database(self):
  self.assertNotIn('database',n.MODULES['immich']);self.assertEqual(n.MODULES['postgres'],['database'])
 def test_immich_stop_keeps_shared_database_and_joplin(self):
  with patch.object(n,'inventory',return_value=[]),patch.object(n,'audit'),patch.object(n,'compose') as compose:n.stop(self.c,'immich')
  self.assertFalse(compose.called);self.assertIn('postgres',n.autostart(self.c))
 def test_joplin_blocks_unmount_even_if_other_consumers_are_off(self):
  with patch.object(n,'inventory',return_value=[{'service':'joplin','state':'running'}]):
   with self.assertRaisesRegex(RuntimeError,'consumers'):n.unmount(self.c)
 def test_http_cannot_stop_or_restart_shared_database(self):
  for action in ['stop','restart']:
   with self.assertRaises(RuntimeError):n.action(self.c,{'action':action,'module':'postgres'})
 def test_credentials_are_distinct_and_repeatable(self):
  first=db.credentials(self.c);self.assertNotEqual(first['immich'],first['joplin']);self.assertEqual(db.credentials(self.c),first)
 def test_old_admin_credentials_require_explicit_migration(self):
  path=Path(self.c['host'])/'config/immich.env';path.write_text('DB_PASSWORD=old-administrator\n')
  with self.assertRaises(RuntimeError):db.credentials(self.c)
  self.assertEqual(path.read_text(),'DB_PASSWORD=old-administrator\n')
  db.credentials(self.c,migrate=True);self.assertNotIn('old-administrator',path.read_text());self.assertTrue((Path(self.c['host'])/'state/immich.env.before-shared-postgres').exists())
if __name__=='__main__':unittest.main(verbosity=2)
