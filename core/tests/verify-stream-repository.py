#!/usr/bin/env python3
"""Pure fixture encryption, commit, interruption, replay and bounded-read tests."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import io,tempfile,unittest
from unittest.mock import patch
from nas_stream import Repository,Journal,Missing,IntegrityError

PASSWORD=b'fixture-key-material-never-used-for-production'
A='a'*32;B='b'*32
class Store:
 def __init__(self):self.objects={};self.reads=[];self.writes=[];self.fail=None;self.truncate=False;self.listings=0
 def empty(self):self.listings+=1;return not self.objects
 def get(self,name,limit):
  self.reads.append(name)
  if name not in self.objects:raise Missing(name)
  data=self.objects[name]
  if len(data)>limit:raise IntegrityError('Read budget exceeded')
  return data
 def put(self,name,data):
  self.writes.append(name);self.objects[name]=data
  if self.fail==name:
   self.fail=None
   if self.truncate:self.objects[name]=data[:10]
   raise OSError('fixture interrupted upload')

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=Store();self.repo=Repository(self.store,PASSWORD,initialize=True);self.journal=Journal(Path(self.tmp.name)/'journal.sqlite')
 def tearDown(self):self.journal.close();self.tmp.cleanup()
 def upload(self,ident=A,payload=b'original payload'*9,parent=None,**kw):return self.repo.upload('data',ident,io.BytesIO(payload),self.journal,producer={'version':'fixture-1'},parent=parent,chunk_size=32,**kw)
 def restored(self,ident=A):
  result=io.BytesIO();self.repo.restore(ident,result.write);return result.getvalue()
 def test_round_trip_paged_without_history_listing(self):
  with patch('nas_stream.PAGE',2):m=self.upload()
  self.assertGreater(len(m['pages']),1);self.assertEqual(self.restored(),b'original payload'*9);self.assertEqual(self.store.listings,1)
  self.assertFalse(any(b'original payload' in b for b in self.store.objects.values()))
  self.assertEqual(self.journal.db.execute('SELECT COUNT(*) FROM chunk').fetchone()[0],0)
 def test_password_ownership_and_path_authentication(self):
  with self.assertRaises(IntegrityError):Repository(self.store,b'x'*32,expected=self.repo.id)
  self.upload();self.store.objects['chunks/'+A+'/0']=self.store.objects['chunks/'+A+'/1']
  with self.assertRaises(IntegrityError):self.restored()
 def test_nonempty_foreign_destination_refused(self):
  store=Store();store.objects['foreign']=b'keep'
  with self.assertRaises(IntegrityError):Repository(store,PASSWORD,initialize=True)
  self.assertEqual(store.objects,{'foreign':b'keep'})
 def test_changed_repository_identity_refused(self):
  with self.assertRaises(IntegrityError):Repository(self.store,PASSWORD,expected='f'*32)
 def test_changed_producer_refuses_resume(self):
  self.store.fail='chunks/'+A+'/1'
  with self.assertRaises(OSError):self.upload()
  with self.assertRaises(IntegrityError):self.repo.upload('data',A,io.BytesIO(b'original payload'*9),self.journal,producer={'version':'fixture-2'},chunk_size=32)
 def test_changed_regenerated_prefix_refused(self):
  self.store.fail='chunks/'+A+'/1'
  with self.assertRaises(OSError):self.upload()
  with self.assertRaises(IntegrityError):self.upload(payload=b'changed payload'*9)
 def test_producer_failure_cannot_publish(self):
  self.upload()
  with self.assertRaisesRegex(RuntimeError,'producer failed'):
   self.upload(B,b'new payload',parent=A,finish=lambda:(_ for _ in ()).throw(RuntimeError('producer failed')))
  self.assertEqual(self.repo.head('data')['commit'],A);self.assertEqual(self.restored(),b'original payload'*9)
 def test_remote_rollback_detected(self):
  self.upload();self.upload(B,b'new payload',parent=A)
  self.store.objects.pop('heads/data/0')
  with self.assertRaisesRegex(IntegrityError,'rollback'):self.repo.head('data',minimum=2)
 def test_empty_and_invalid_parent_refused(self):
  with self.assertRaises(IntegrityError):self.upload(payload=b'')
  with self.assertRaises(IntegrityError):self.upload(B,parent='c'*32)
 def test_short_reads_do_not_change_chunk_boundaries(self):
  class Short(io.BytesIO):
   def read(self,n=-1):return super().read(min(n,3))
  self.store.fail='chunks/'+A+'/2'
  with self.assertRaises(OSError):self.upload()
  confirmed=self.store.writes.count('chunks/'+A+'/0')
  self.repo.upload('data',A,Short(b'original payload'*9),self.journal,producer={'version':'fixture-1'},chunk_size=32)
  self.assertEqual(self.store.writes.count('chunks/'+A+'/0'),confirmed);self.assertEqual(self.restored(),b'original payload'*9)
 def test_every_upload_boundary_retains_prior_and_resumes(self):
  self.upload();old=dict(self.store.objects)
  for name in ['chunks/'+B+'/'+str(i) for i in range(3)]+['pages/'+B+'/0','commits/'+B,'heads/data/0']:
   with self.subTest(name=name):
    self.store.objects=dict(old);self.store.fail=name;self.store.truncate=True
    self.journal.finish(B)
    with self.assertRaises(OSError):self.upload(B,b'next payload'*8,parent=A)
    self.assertEqual(self.restored(),b'original payload'*9)
    self.upload(B,b'next payload'*8,parent=A)
    self.assertEqual(self.restored(B),b'next payload'*8);self.assertEqual(self.repo.head('data')['commit'],B)
 def test_prepared_commit_recovers_after_local_journal_loss(self):
  self.upload();self.store.fail='heads/data/0';self.store.truncate=True
  with self.assertRaises(OSError):self.upload(B,b'new committed bytes',parent=A)
  self.journal.finish(B)
  m=self.repo.recover('data',B,self.journal,producer={'version':'fixture-1'},parent=A,minimum=1)
  self.assertEqual(m['id'],B);self.assertEqual(self.restored(B),b'new committed bytes')
 def test_prepared_commit_cannot_recover_with_changed_intent(self):
  self.upload();self.store.fail='heads/data/0'
  with self.assertRaises(OSError):self.upload(B,b'new committed bytes',parent=A)
  with self.assertRaises(IntegrityError):self.repo.recover('data',B,self.journal,producer={'version':'different'},parent=A)
 def test_shared_journal_isolated_between_cloud_repositories(self):
  path=Path(self.tmp.name)/'shared.sqlite';one=Journal(path,namespace='1'*32);two=Journal(path,namespace='2'*32)
  try:
   one.begin(A,{'repository':1});two.begin(A,{'repository':2});one.acknowledged(A,0,b'first');two.acknowledged(A,0,b'second');one.finish(A)
   self.assertIsNone(one.chunk(A,0));self.assertEqual(two.chunk(A,0)[0],6)
  finally:one.close();two.close()
 def test_reads_are_bounded(self):
  self.upload();self.store.objects['chunks/'+A+'/0']=b'x'*1000
  with self.assertRaisesRegex(IntegrityError,'budget'):self.restored()

if __name__=='__main__':unittest.main()
