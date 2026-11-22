#!/usr/bin/env python3
"""Canonical IDs, storage contracts and alias locking without production mutation."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import json,re,tempfile,unittest
from unittest.mock import patch
import nas_apps as a
import nasctl as n

class Tests(unittest.TestCase):
 def test_canonical_ids_are_stable_and_valid(self):
  self.assertEqual(set(a.APPS),{'immich','files','shared','joplin','git','platform'})
  self.assertTrue(all(re.fullmatch('[a-z][a-z0-9-]*',key) for key in a.APPS))
 def test_owned_data_paths_match_their_application(self):
  for key,app in a.APPS.items():
   for path in app.get('data_paths',[]):
    if path=='.':self.assertTrue(app.get('data_read_only'));continue
    self.assertTrue(path==key or path.startswith(key+'-'))
 def test_database_and_role_match_application_id(self):
  for key,app in a.APPS.items():
   if app.get('database'):self.assertEqual(app['database'],key);self.assertEqual(app['database_user'],key)
 def test_registry_paths_match_an_application_namespace(self):
  registry=json.loads((Path(__file__).resolve().parents[1]/'templates/data-access.json').read_text())
  for rule in registry['rules']:
   if rule['path']=='.':continue
   self.assertTrue(any(rule['path']==key or rule['path'].startswith(key+'-') for key in a.APPS))
 def test_storage_flags_do_not_depend_on_runtime_state(self):
  modules={m:{'state':'stopped','containers':[]} for m in n.MODULES}
  result=a.snapshot({},modules,{})
  self.assertEqual({key for key,app in result.items() if app['storage']['database']},{'immich','joplin'})
  self.assertEqual({key for key,app in result.items() if app['storage']['data']},{'immich','shared','joplin','git'})
 def test_legacy_power_routes_to_same_application(self):
  with patch.object(n,'start') as start:
   a.power({},'knowledge',True);a.power({},'joplin',True)
   self.assertEqual([call.args[1] for call in start.call_args_list],['knowledge','knowledge'])
  self.assertEqual(a.canonical_app('syncthing'),'shared')
 def test_legacy_and_canonical_requests_share_one_lock(self):
  with tempfile.TemporaryDirectory() as directory,patch.object(n,'LOCK_ROOT',Path(directory)):
   with n.application_lock('knowledge',{'action':'app-power','enabled':True}):
    self.assertIn('joplin',n.application_operations())
    with self.assertRaises(RuntimeError):
     with n.application_lock('joplin',{'action':'app-power','enabled':False}):pass
if __name__=='__main__':unittest.main(verbosity=2)
