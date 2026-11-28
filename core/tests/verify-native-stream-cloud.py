#!/usr/bin/env python3
"""Opt-in synthetic cloud test in a new isolated namespace; never user payloads."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import argparse,json,secrets,subprocess,time,shutil
import nasctl,nas_storage,nas_cloud
from nas_backup_control import operation,check
from nas_btrfs_stream import Snapshots
from nas_stream import Repository,Journal
from nas_objects import RcloneStore

p=argparse.ArgumentParser();p.add_argument('--run',action='store_true');p.add_argument('--report',type=Path,required=True);a=p.parse_args();assert a.run and not a.report.exists()
c=nasctl.cfg();nas_storage.verify(c);accounts=nas_cloud.metadata(c)['accounts'];assert accounts
account=next(x for x in accounts if x.get('repository_owned'));nonce=secrets.token_hex(16);namespace='native-test-'+nonce;root=Path(c['data_storage']['mount'])/('.native-cloud-test-'+nonce);assert not root.exists();root.mkdir(mode=0o700)
source=root/'generated';created=[];snapshots=None;journal=None;outcome={'state':'running','namespace':namespace};lifecycle=None

def run(args):return subprocess.check_output(list(map(str,args)),text=True,stderr=subprocess.STDOUT).strip()
try:
 # Allow existing short administrative operations to finish. Once held, this
 # shared lock still permits application operations while guarding safe eject.
 deadline=time.monotonic()+300
 while True:
  try:lifecycle=nasctl.lock(shared=True);lifecycle.__enter__();break
  except RuntimeError:
   if time.monotonic()>deadline:raise RuntimeError('Existing maintenance did not finish; cloud fixture not started')
   time.sleep(.5)
 with operation(c,'cloud'):
  run(['btrfs','subvolume','create',source]);created.append(source);(source/'note.txt').write_bytes(b'generated fixture before\n'*600);(source/'delete.txt').write_bytes(b'fixture delete');(source/'rename.txt').write_bytes(b'fixture rename')
  fixture={**c,'data':str(source),'data_storage':{**c['data_storage'],'mount':str(root)}}
  def verify(value):nas_storage.verify(c);assert source.stat().st_ino==256
  snapshots=Snapshots(fixture,verify=verify)
  with RcloneStore(c,account,namespace=namespace) as store:
   repo=Repository(store,b'public-fixture-only-key-no-personal-payload',initialize=True);journal=Journal(Path(c['host'])/'runtime'/('cloud-fixture-'+nonce+'.sqlite'),namespace=repo.id)
   parent=None;commits=[];injected=False
   for phase in range(3):
    if phase==1:(source/'note.txt').write_bytes(b'generated fixture after');(source/'delete.txt').unlink();(source/'rename.txt').rename(source/'renamed.txt')
    if phase==2:(source/'third.txt').write_bytes(b'third generated point')
    point=secrets.token_hex(16);snapshots.create(point);created.append(snapshots.path(point))
    if phase==0:
     original=store.put
     def fail_once(name,data):
      global injected
      original(name,data)
      if name=='chunks/'+point+'/1' and not injected:injected=True;raise OSError('fixture connection lost after remote write')
     store.put=fail_once
     producer,identity=snapshots.send(point)
     try:
      with producer:repo.upload('data',point,producer,journal,producer=identity,finish=producer.finish,check=check,chunk_size=4096)
     except OSError:pass
     else:raise AssertionError('Expected owned synthetic interruption')
     store.put=original;assert repo.head('data') is None
    producer,identity=snapshots.send(point,parent)
    with producer:manifest=repo.upload('data',point,producer,journal,producer=identity,parent=parent,finish=producer.finish,check=check,chunk_size=4096)
    restored=__import__('hashlib').sha256();repo.restore(point,restored.update,check=check);assert restored.hexdigest()==manifest['sha256']
    assert repo.head('data')['commit']==point;commits.append(point);parent=point
   # The third commit overwrites one head slot; the initial immutable point is
   # still fully readable and authenticated, independently of current HEAD.
   count=0
   def old(data):
    global count
    count+=len(data)
   repo.restore(commits[0],old,check=check);assert count>0
   outcome={'state':'PASS','namespace':namespace,'repository':repo.id,'checks':['new encrypted namespace ownership','real upload/download authentication and hashes','interruption and confirmed-prefix reuse','baseline and two Btrfs increments','head overwrite visibility','original committed stream retained'],'note':'Only synthetic fixture objects uploaded; namespace retained for inspection, no production repository objects removed'}
except BaseException as error:
 outcome.update(state='FAIL',error=str(error));raise
finally:
 if journal:journal.close()
 cleanup=[]
 for target in reversed(created):
  try:
   nas_storage.verify(c);assert target==source or target.parent==root/'.garden-zero-streams';run(['btrfs','subvolume','delete',target])
  except Exception as e:cleanup.append(str(e))
 if not cleanup:
  assert root.parent==Path(c['data_storage']['mount']) and root.name=='.native-cloud-test-'+nonce;shutil.rmtree(root)
 if lifecycle:lifecycle.__exit__(None,None,None)
 if cleanup:outcome.update(state='CLEANUP_FAILED',cleanup_errors=cleanup)
 a.report.write_text(json.dumps(outcome,indent=2));print('Synthetic cloud fixture:',outcome['state'],flush=True)
