#!/usr/bin/env python3
"""Isolated mirror safety/transaction checks; never opens the configured backup target."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import hashlib,io,json,os,tempfile,unittest
from nas_mirror import Mirror,META,canonical

class Source:
 def __init__(self,files,stamp='20260101-010101',directories=()):
  self.id=stamp;self.manifest_sha256=hashlib.sha256(stamp.encode()).hexdigest();self.values={k:v.encode() for k,v in files.items()};self.entries={};self.times={}
  for key,value in self.values.items():
   self.entries[key]={'kind':'file','size':len(value),'sha256':hashlib.sha256(value).hexdigest()}
   for parent in Path(key).parents:
    if str(parent)!='.':self.entries[str(parent)]={'kind':'dir'}
  for key in directories:self.entries[key]={'kind':'dir'}
 def open(self,name):return io.BytesIO(self.values[name])

class Interrupted(Exception):pass
class Tests(unittest.TestCase):
 def setUp(self):
  self.work=tempfile.TemporaryDirectory(prefix='garden-zero-mirror-test-');self.base=Path(self.work.name);self.root=self.base/'target';self.root.mkdir();self.state=self.base/'checkpoint.json';self.key=b'x'*32;self.identity={'volume_guid':'fixture','root':'fixture'}
 def tearDown(self):self.work.cleanup()
 def mirror(self,point=None):
  def fail(event):
   if event==point:raise Interrupted(event)
  return Mirror(self.root,self.state,self.key,self.identity,fail)
 def before(self):return {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
 def test_incremental_and_delete(self):
  first=Source({'data/a':'same','data/b':'delete','data/nested/old':'old'})
  self.mirror().sync(first);ino=(self.root/'data/a').stat().st_ino;mtime=(self.root/'data/a').stat().st_mtime_ns
  result=self.mirror().sync(Source({'data/a':'same','data/c':'new'}))
  self.assertEqual(result['copied_files'],1);self.assertEqual(result['deleted_files'],2);self.assertEqual(result['unchanged_files'],1)
  self.assertEqual((self.root/'data/a').stat().st_ino,ino);self.assertEqual((self.root/'data/a').stat().st_mtime_ns,mtime);self.assertFalse((self.root/'data/nested').exists())
 def test_unchanged_backup_writes_no_content(self):
  s=Source({'data/a':'value'});self.mirror().sync(s);r=self.mirror().sync(s);self.assertEqual(r['written_bytes'],0);self.assertEqual(r['copied_files'],0)
 def test_nonempty_unknown_refused_without_changes(self):
  (self.root/'mine').write_text('private');before=self.before()
  with self.assertRaises(RuntimeError):self.mirror().sync(Source({'data/a':'v'}))
  self.assertEqual(self.before(),before);self.assertFalse(self.state.exists())
 def test_target_mutation_refuses_all_writes(self):
  self.mirror().sync(Source({'data/a':'old'}));(self.root/'data/a').write_text('bad');before=self.before()
  with self.assertRaises(RuntimeError):self.mirror().sync(Source({'data/a':'new','data/b':'new'}))
  self.assertEqual(self.before(),before)
 def test_same_size_and_restored_mtime_mutation_detected(self):
  self.mirror().sync(Source({'data/a':'old'}));p=self.root/'data/a';s=p.stat();p.write_text('bad');os.utime(p,ns=(s.st_atime_ns,s.st_mtime_ns))
  with self.assertRaises(RuntimeError):self.mirror().check()
 def test_extra_file_refused(self):
  self.mirror().sync(Source({'data/a':'old'}));(self.root/'extra').write_text('keep');before=self.before()
  with self.assertRaises(RuntimeError):self.mirror().sync(Source({}))
  self.assertEqual(self.before(),before)
 def test_missing_file_refused(self):
  self.mirror().sync(Source({'data/a':'old'}));(self.root/'data/a').unlink()
  with self.assertRaises(RuntimeError):self.mirror().check()
 def test_wrong_identity_refused(self):
  self.mirror().sync(Source({'data/a':'old'}))
  with self.assertRaises(RuntimeError):Mirror(self.root,self.state,self.key,{'volume_guid':'wrong'}).check()
 def test_manifest_forgery_refused(self):
  self.mirror().sync(Source({'data/a':'old'}));p=self.root/META/'manifest.json';v=json.loads(p.read_text());v['payload']['entries']={};p.write_text(json.dumps(v))
  with self.assertRaises(RuntimeError):self.mirror().check()
 def test_missing_checkpoint_refused(self):
  self.mirror().sync(Source({'data/a':'old'}));self.state.unlink()
  with self.assertRaises(RuntimeError):self.mirror().check()
 def test_replay_old_signed_manifest_refused(self):
  self.mirror().sync(Source({'data/a':'old'}));p=self.root/META/'manifest.json';old=p.read_bytes();self.mirror().sync(Source({'data/a':'new'}));p.write_bytes(old)
  with self.assertRaises(RuntimeError):self.mirror().check()
 def test_file_and_directory_conversion(self):
  self.mirror().sync(Source({'data/a':'old','data/b/child':'old'}));self.mirror().sync(Source({'data/a/child':'new','data/b':'new'}));self.assertTrue((self.root/'data/a').is_dir());self.assertEqual((self.root/'data/b').read_text(),'new')
 def test_empty_directories_preserved_and_deleted(self):
  self.mirror().sync(Source({},directories=['empty']));self.assertTrue((self.root/'empty').is_dir());self.mirror().sync(Source({}));self.assertFalse((self.root/'empty').exists())
 def test_symlink_refused(self):
  self.mirror().sync(Source({'data/a':'old'}));(self.root/'data/a').unlink();(self.root/'data/a').symlink_to(self.base/'outside')
  with self.assertRaises(RuntimeError):self.mirror().check()
 def test_hardlink_refused(self):
  self.mirror().sync(Source({'data/a':'old'}));os.link(self.root/'data/a',self.base/'outside')
  with self.assertRaises(RuntimeError):self.mirror().check()
 def test_target_changed_during_staging_refused(self):
  self.mirror().sync(Source({'data/a':'old'}))
  def mutate(event):
   if event=='staged':(self.root/'data/a').write_text('bad')
  with self.assertRaises(RuntimeError):Mirror(self.root,self.state,self.key,self.identity,mutate).sync(Source({'data/a':'new'}))
  self.assertEqual((self.root/'data/a').read_text(),'bad')
 def test_resume_each_commit_boundary(self):
  for point in ['staged','deleted','replaced','manifest','checkpoint']:
   with self.subTest(point=point):
    # Fresh independent transaction for every failure boundary.
    target=self.base/point;target.mkdir();state=self.base/(point+'.json')
    def fail(event):
     if event==point:raise Interrupted(event)
    Mirror(target,state,self.key,self.identity).sync(Source({'data/a':'old','data/remove':'remove'}))
    desired=Source({'data/a':'new','data/b':'new'},'20260102-010101')
    with self.assertRaises(Interrupted):Mirror(target,state,self.key,self.identity,fail).sync(desired)
    Mirror(target,state,self.key,self.identity).sync(desired)
    self.assertEqual((target/'data/a').read_text(),'new');self.assertFalse((target/'data/remove').exists());self.assertFalse((target/META/'pending.json').exists())
 def test_interrupted_external_mutation_refused(self):
  self.mirror().sync(Source({'data/a':'old'}));s=Source({'data/a':'new'})
  with self.assertRaises(Interrupted):self.mirror('staged').sync(s)
  (self.root/'data/a').write_text('bad')
  with self.assertRaises(RuntimeError):self.mirror().sync(s)
 def test_pending_old_source_required(self):
  self.mirror().sync(Source({'data/a':'old'}))
  with self.assertRaises(Interrupted):self.mirror('staged').sync(Source({'data/a':'new'}))
  with self.assertRaises(RuntimeError):self.mirror().sync(Source({'data/a':'other'},'20260102-010101'))

 def test_complete_previous_generation_survives_update(self):
  old=Source({'data/a':'old','data/b':'deleted'});self.mirror().sync(old)
  self.mirror().sync(Source({'data/a':'new','data/c':'added'}))
  history=self.root/META/'history/generation-00000000000000000001/content'
  self.assertEqual((history/'data/a').read_text(),'old');self.assertEqual((history/'data/b').read_text(),'deleted')
  self.assertFalse((self.root/'data/b').exists());self.assertEqual(self.mirror().check()['history_snapshots'],1)
 def test_history_reuses_unchanged_file_storage(self):
  self.mirror().sync(Source({'data/a':'same','data/b':'old'}));self.mirror().sync(Source({'data/a':'same','data/b':'new'}))
  history=self.root/META/'history/generation-00000000000000000001/content'
  self.assertEqual((history/'data/a').stat().st_ino,(self.root/'data/a').stat().st_ino)
  self.mirror().sync(Source({'data/a':'changed','data/b':'new'}))
  self.assertEqual((history/'data/a').read_text(),'same');self.assertEqual(self.mirror().check()['history_snapshots'],2)
 def test_previous_complete_backup_at_every_interruption(self):
  for point in ['staged','history-file','history','deleted','replaced','manifest','checkpoint']:
   with self.subTest(point=point):
    target=self.base/('keep-'+point);target.mkdir();state=self.base/('keep-'+point+'.json')
    def fail(event):
     if event==point:raise Interrupted(event)
    old=Source({'data/a':'old','data/b':'deleted'});new=Source({'data/a':'new','data/c':'added'})
    Mirror(target,state,self.key,self.identity).sync(old)
    with self.assertRaises(Interrupted):Mirror(target,state,self.key,self.identity,fail).sync(new)
    candidates=[target,target/META/'history/generation-00000000000000000001/content']
    self.assertTrue(any(all((base/key).is_file() and (base/key).read_bytes()==value for key,value in old.values.items()) for base in candidates))
    Mirror(target,state,self.key,self.identity).sync(new)
 def test_purge_only_after_complete_new_backup(self):
  old=Source({'data/a':'old','data/b':'deleted'});new=Source({'data/a':'new'})
  self.mirror().sync(old)
  with self.assertRaises(Interrupted):self.mirror('replaced').sync(new,purge_history=True)
  history=self.root/META/'history/generation-00000000000000000001/content'
  self.assertEqual((history/'data/b').read_text(),'deleted');self.assertEqual(self.mirror().read(self.state)['generation'],1)
  result=self.mirror().sync(new,purge_history=True);self.assertEqual(result['history_snapshots'],0);self.assertEqual(result['purged_snapshots'],1);self.assertEqual((self.root/'data/a').read_text(),'new')
 def test_interrupted_cleanup_keeps_new_complete_backup(self):
  self.mirror().sync(Source({'data/a':'old','data/b':'deleted'}));new=Source({'data/a':'new'})
  with self.assertRaises(Interrupted):self.mirror('purged-file').sync(new,purge_history=True)
  self.assertEqual((self.root/'data/a').read_text(),'new');self.assertEqual(self.mirror().read(self.state)['generation'],2)
  self.mirror().sync(new,purge_history=True);self.assertEqual(self.mirror().check()['history_snapshots'],0)
 def test_history_tampering_refused(self):
  self.mirror().sync(Source({'data/a':'old'}));self.mirror().sync(Source({'data/a':'new'}))
  old=self.root/META/'history/generation-00000000000000000001/content/data/a';old.write_text('bad');before=self.before()
  with self.assertRaises(RuntimeError):self.mirror().sync(Source({'data/a':'next'}))
  self.assertEqual(self.before(),before)
 def test_hardlinked_staging_file_does_not_modify_external_file(self):
  self.mirror().sync(Source({'data/a':'old'}));new=Source({'data/a':'new'})
  with self.assertRaises(Interrupted):self.mirror('staged').sync(new)
  stage=self.root/META/'stage'/hashlib.sha256(b'data/a').hexdigest();stage.unlink();outside=self.base/'external';outside.write_text('private');os.link(outside,stage)
  self.mirror().sync(new);self.assertEqual(outside.read_text(),'private');self.assertEqual((self.root/'data/a').read_text(),'new')

 def test_complete_staged_files_are_reused(self):
  self.mirror().sync(Source({'data/a':'old'}));new=Source({'data/a':'new'})
  with self.assertRaises(Interrupted):self.mirror('staged').sync(new)
  result=self.mirror().sync(new);self.assertEqual(result['written_bytes'],0);self.assertEqual(result['reused_staged_files'],1)
 def test_partial_staged_file_resumes_after_verified_prefix(self):
  self.mirror().sync(Source({'data/a':'old'}));new=Source({'data/a':'z'*(3*1024*1024)})
  with self.assertRaises(Interrupted):self.mirror('copy-chunk').sync(new)
  stage=self.root/META/'stage'/hashlib.sha256(b'data/a').hexdigest();offset=stage.stat().st_size;self.assertGreater(offset,0)
  result=self.mirror().sync(new);self.assertEqual(result['resumed_bytes'],offset);self.assertEqual(result['written_bytes'],len(new.values['data/a'])-offset)
 def test_corrupt_partial_file_is_not_trusted(self):
  self.mirror().sync(Source({'data/a':'old'}));new=Source({'data/a':'z'*(2*1024*1024)})
  with self.assertRaises(Interrupted):self.mirror('copy-chunk').sync(new)
  stage=self.root/META/'stage'/hashlib.sha256(b'data/a').hexdigest();stage.write_bytes(b'x'*stage.stat().st_size)
  result=self.mirror().sync(new);self.assertEqual(result['resumed_bytes'],0);self.assertEqual(result['written_bytes'],len(new.values['data/a']))

if __name__=='__main__':unittest.main(verbosity=2)
