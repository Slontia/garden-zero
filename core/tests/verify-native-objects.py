#!/usr/bin/env python3
"""Transport fixtures only: no Docker containers, credentials or clouds accessed."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import subprocess,unittest
from unittest.mock import patch
from nas_objects import RcloneStore,object_path
from nas_stream import Missing,IntegrityError

class Tests(unittest.TestCase):
 def store(self):
  store=RcloneStore({'host':'/fixture','project':'fixture'},{'id':'fixture-1234'});store.active=True;return store
 def test_object_lookup_has_fixed_directory_depth(self):
  paths=[object_path('chunks/'+('a'*32)+'/'+str(i)) for i in range(10000)]
  self.assertEqual(len(set(paths)),10000)
  for name in paths:self.assertEqual(len(name.split('/')),6)
  self.assertEqual(object_path('repository.json'),'native-v1/repository.json')
 def test_unsafe_account_and_object_names_rejected(self):
  for name in ['../escape','/root','x/../secret','a//b']:
   with self.assertRaises(IntegrityError):object_path(name)
  with self.assertRaises(RuntimeError):RcloneStore({'host':'/fixture'},{'id':'../wrong'})
 def test_bounded_read_and_single_connection_flags(self):
  store=self.store()
  with patch('nas_objects.run',return_value=subprocess.CompletedProcess([],0,b'data',b'')) as run:
   self.assertEqual(store.get('chunks/abc/0',10),b'data');args=run.call_args.args[0]
   self.assertIn('--head',args);self.assertEqual(args[args.index('--head')+1],'11');self.assertIn('--checkers',args)
 def test_upload_declares_size_without_payload_on_cli(self):
  store=self.store()
  with patch('nas_objects.run',return_value=subprocess.CompletedProcess([],0,b'',b'')) as run:
   store.put('chunks/abc/0',b'private payload');args=run.call_args.args[0]
   self.assertEqual(args[args.index('--size')+1],str(len(b'private payload')));self.assertNotIn('private payload',args)
   self.assertEqual(run.call_args.kwargs['input'],b'private payload')
 def test_missing_is_distinguished_from_network_auth_failure(self):
  store=self.store()
  for code in [3,4]:
   with patch('nas_objects.run',return_value=subprocess.CompletedProcess([],code,b'',b'')):
    with self.assertRaises(Missing):store.get('commits/abc',100)
  with patch('nas_objects.run',return_value=subprocess.CompletedProcess([],5,b'',b'network failure')):
   with self.assertRaises(RuntimeError):store.get('commits/abc',100)
 def test_unknown_namespace_never_enumerates_its_contents(self):
  store=self.store()
  with patch.object(store,'command',return_value=b'{"IsDir":true}') as cmd:
   self.assertFalse(store.empty());self.assertIn('--stat',cmd.call_args.args[0]);self.assertNotIn('lsf',cmd.call_args.args[0])
 def test_response_budget_and_inactive_transport(self):
  store=self.store()
  with patch('nas_objects.run',return_value=subprocess.CompletedProcess([],0,b'oversized',b'')):
   with self.assertRaises(IntegrityError):store.get('test',1)
  store.active=False
  with self.assertRaises(RuntimeError):store.get('test',1)

if __name__=='__main__':unittest.main()
