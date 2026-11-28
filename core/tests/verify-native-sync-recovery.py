#!/usr/bin/env python3
"""Cross-filesystem recovery fixture; no production API calls or user-file writes."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import tempfile,os,subprocess
from unittest.mock import patch
import nas_sync as sync


def main():
 volume=Path('/run/nas/storage')
 if not volume.is_mount():raise RuntimeError('Requires the mounted native data volume for an isolated fixture')
 with tempfile.TemporaryDirectory(prefix='nas-restore-code-',dir='/run') as code_dir,tempfile.TemporaryDirectory(prefix='.restore-fixture-',dir=volume) as data_dir:
  code=Path(code_dir);data=Path(data_dir);(code/'system/runtime').mkdir(parents=True)
  assert code.stat().st_dev!=data.stat().st_dev,'Fixture must exercise different filesystems'
  shared=data/'shared';archive=shared/'.stversions';(archive/'nested').mkdir(parents=True)
  source=archive/'nested/note~20260102-123456.txt';source.write_bytes(b'archived version')
  os.chown(shared,1000,1000)
  original=shared/'nested/note.txt';original.parent.mkdir();original.write_bytes(b'current version')
  version='2026-01-02T12:34:56+08:00';paused=False
  def api(c,path,body=None,method=None):
   nonlocal paused
   if path=='config/folders/'+sync.FOLDER:
    if body is not None:paused=body['paused'];return
    return {'id':sync.FOLDER,'path':sync.PATH,'type':'sendreceive','paused':paused,'maxConflicts':-1,'versioning':{'type':'simple','params':{'keep':'20'}}}
   if path=='folder/versions?folder='+sync.FOLDER:return {'nested/note.txt':[{'versionTime':version,'size':16}]}
   raise AssertionError(path)
  fixture={'root':str(code),'host':str(code/'system'),'data':str(data),'critical_outer_free':0}
  with patch('nasctl.volume'),patch.object(sync,'api',side_effect=api):
   result=sync.restore_copy(fixture,'nested/note.txt',version)
   assert (shared/result['restored_path']).read_bytes()==b'archived version'
   assert original.read_bytes()==b'current version' and source.read_bytes()==b'archived version'
   assert not paused and result['sync_resumed']
   recovered=shared/result['restored_path'];assert recovered.stat().st_uid==1000 and recovered.parent.stat().st_uid==1000
   subprocess.run([sys.executable,'-B','-c',"import os,sys;from pathlib import Path;os.chdir(sys.argv[1]);os.setgroups([]);os.setgid(1000);os.setuid(1000);assert Path(sys.argv[2]).read_bytes()==b'archived version'",str(shared),result['restored_path']],check=True)
   assert not list(archive.glob('.restore-copy-*')) and not list((code/'system/runtime').iterdir())
   # A publication failure must remove its staged payload and keep sync paused.
   with patch('os.rename',side_effect=OSError('fixture publish failure')):
    try:sync.restore_copy(fixture,'nested/note.txt',version)
    except RuntimeError as error:assert 'fixture publish failure' in str(error)
    else:raise AssertionError('Expected publication failure')
   assert paused and not list(archive.glob('.restore-copy-*'))
   assert original.read_bytes()==b'current version' and source.read_bytes()==b'archived version'
 print('PASS: cross-filesystem restore, original/history preservation, staged-payload cleanup and safe pause on failure')

if __name__=='__main__':main()
