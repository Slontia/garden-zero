#!/usr/bin/env python3
"""Real scoped filesystem recovery with fake REST catalog; never restore a production file."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import datetime,hashlib,json,os,uuid
from pathlib import Path
from unittest.mock import patch
from nasctl import cfg
import nas_sync as sync

def main():
 c=cfg();work=Path(c['root'])/'system/runtime'/('sync-recovery-check-'+uuid.uuid4().hex[:8]);data=work/'data';shared=data/'shared';archive=shared/'.stversions';archive.mkdir(parents=True);fixture={**c,'data':str(data)};checks=[]
 version='2026-01-02T12:34:56+08:00';catalog={};paused=False;events=[]
 def api(config,path,body=None,method=None):
  nonlocal paused
  if path=='config/folders/'+sync.FOLDER:
   if body is not None:paused=body['paused'];events.append(('paused',paused));return
   return {'id':sync.FOLDER,'path':sync.PATH,'type':'sendreceive','paused':paused,'maxConflicts':-1,'versioning':{'type':'simple','params':{'keep':'20'}}}
  if path=='folder/versions?folder='+sync.FOLDER:return catalog
  raise AssertionError(path)
 def add(name,old=b'old contents',current=b'current contents'):
  parts=sync.relative_parts(name);leaf=parts[-1];dot=leaf.rfind('.');tagged=leaf[:dot]+'~20260102-123456'+leaf[dot:] if dot>=0 else leaf+'~20260102-123456'
  p=archive.joinpath(*parts[:-1],tagged);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(old)
  q=shared/name;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(current);catalog[name]=[{'versionTime':version,'modTime':version,'size':len(old)}];return p,q
 with patch.object(sync,'api',side_effect=api):
  for name in ('document.txt','nested/照片.tar.gz','.env'):
   source,original=add(name);result=sync.restore_copy(fixture,name,version)
   assert (shared/result['restored_path']).read_bytes()==b'old contents'
   assert original.read_bytes()==b'current contents' and source.read_bytes()==b'old contents'
   assert result['sha256']==hashlib.sha256(b'old contents').hexdigest() and result['sync_resumed'] and not paused
  checks.append('real normal, nested Unicode/multiple extension and dotfile archives restore into unique new directories; original/history unchanged')
  paused=True;source,original=add('paused.txt');r=sync.restore_copy(fixture,'paused.txt',version);assert paused and not r['sync_resumed'];paused=False
  checks.append('pre-existing shared pause remains paused')
  for name in ('../escape','/outside','nested/../escape','.stversions/secret','C:/secret','a\\b'):
   try:sync.restore_copy(fixture,name,version);raise AssertionError('Unsafe path accepted')
   except RuntimeError:pass
  try:sync.restore_copy(fixture,'document.txt','2020-01-01T00:00:00Z');raise AssertionError('Unknown version accepted')
  except RuntimeError:pass
  checks.append('path escapes/metadata and unlisted timestamps rejected')
  source,original=add('link.txt');source.unlink();source.symlink_to(original)
  try:sync.restore_copy(fixture,'link.txt',version);raise AssertionError('Symlink accepted')
  except RuntimeError:pass
  assert not paused and original.read_bytes()==b'current contents'
  checks.append('archive symlink fails without changing original or sync state')
  source,original=add('mismatch.txt');source.write_bytes(b'bad')
  try:sync.restore_copy(fixture,'mismatch.txt',version);raise AssertionError('Size mismatch accepted')
  except RuntimeError:pass
  assert not paused;checks.append('archive size mismatch rejected before pausing')
  report=sync.history(fixture);assert report['total']>=3 and report['restore_mode']=='new_copy';checks.append('version catalog lists size/timestamp and new-copy mode')
 report={'state':'PASS','checks':checks,'work':str(work),'limitations':['real NTFS fixture IO with simulated REST catalog; official Syncthing catalog integration checked separately','production user files not restored or modified']}
 (work/'result.json').write_text(json.dumps(report,indent=2)+'\n');(Path(c['root'])/'system/runtime/acceptance/sync-recovery.json').write_text(json.dumps(report,indent=2)+'\n');print('PASS:',len(checks),'safe recovery checks; originals and archived copies preserved')
if __name__=='__main__':main()
