#!/usr/bin/env python3
"""Opt-in encrypted Btrfs send/receive fixture on two disposable regular images."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import argparse,os,stat,subprocess,hashlib,json,time,secrets
from nas_stream import Repository,Journal,Missing
from nas_btrfs_stream import Snapshots
from nas_snapshot_budget import Budget

p=argparse.ArgumentParser();p.add_argument('--run',action='store_true');p.add_argument('--workspace',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args()
base=a.workspace.resolve();report=a.report.resolve()
assert a.run and os.geteuid()==0 and base.parent==Path('/run/nas') and base.name.startswith('stream-eval-') and base.is_dir() and not report.exists()
assert os.readlink('/proc/self/ns/mnt')!=os.readlink('/proc/1/ns/mnt'),'Requires a private mount namespace'
work=base/'fixture';work.mkdir();loops=[];mounts=[];results=[];journal=None

def run(args):return subprocess.check_output(list(map(str,args)),text=True,stderr=subprocess.STDOUT).strip()
def files(root):return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
class Store:
 def __init__(self):self.objects={};self.fail=None
 def empty(self):return not self.objects
 def get(self,name,limit):
  if name not in self.objects:raise Missing(name)
  data=self.objects[name];assert len(data)<=limit;return data
 def put(self,name,data):
  self.objects[name]=data
  if name==self.fail:self.fail=None;raise OSError('fixture disconnected transport')
try:
 run(['mount','-t','tmpfs','-o','size=256m,nosuid,nodev','tmpfs',work]);mounts.append(work)
 for name in ['source','restore']:
  image=work/(name+'.img');fd=os.open(image,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600);os.ftruncate(fd,512*1024**2);os.close(fd);assert stat.S_ISREG(image.stat().st_mode)
  run(['mkfs.btrfs','-q','--nodiscard','-d','single','-m','single',image]);loop=run(['losetup','--find','--show',image]);loops.append((loop,image));target=work/name;target.mkdir();run(['mount','-t','btrfs',loop,target]);mounts.append(target)
 source=work/'source';target=work/'restore';live=source/'data';run(['btrfs','subvolume','create',live]);uuid=run(['findmnt','-n','-o','UUID','--mountpoint',source])
 config={'data':str(live),'data_storage':{'mount':str(source),'uuid':uuid}}
 def verify(c):assert run(['findmnt','-n','-o','UUID','--mountpoint',source])==uuid and live.stat().st_ino==256
 snapshots=Snapshots(config,verify=verify);budget=Budget(snapshots,old_blocks=64*1024**2,minimum_free=64*1024**2);budget.prepare();store=Store();repo=Repository(store,b'fixture-encryption-key-material-123456',initialize=True);journal=Journal(work/'journal.sqlite')
 def receive(ident):
  process=subprocess.Popen(['btrfs','receive',str(target)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  try:repo.restore(ident,process.stdin.write);process.stdin.close();assert process.wait(timeout=60)==0,process.stderr.read().decode()
  finally:
   if process.poll() is None:process.terminate();process.wait(timeout=10)
   process.stdout.close();process.stderr.close()
 def upload(ident,parent=None):
  producer,identity=snapshots.send(ident,parent)
  with producer:return repo.upload('data',ident,producer,journal,producer=identity,parent=parent,finish=producer.finish,chunk_size=256*1024)
 def populate(start,end):
  for n in range(start,end):
   d=live/('d%02d'%(n%32));d.mkdir(exist_ok=True);(d/('f%06d.txt'%n)).write_bytes(('fixture-%d'%n).encode())
 (live/'large.bin').write_bytes(os.urandom(8*1024**2));populate(0,1000)
 for count in [1000,10000]:
  if count==10000:populate(1000,10000)
  first=secrets.token_hex(16);snapshots.create(first,qgroup=budget.group)
  if count==1000:
   store.fail='chunks/'+first+'/2'
   try:upload(first)
   except OSError:pass
   else:raise AssertionError('Expected upload interruption')
   assert repo.head('data') is None
  started=time.monotonic();full=upload(first);receive(first);assert files(snapshots.path(first))==files(target/snapshots.path(first).name)
  old=files(target/snapshots.path(first).name)
  for n in range(10):(live/('d%02d'%(n%32))/('f%06d.txt'%n)).write_bytes(b'changed')
  with (live/'large.bin').open('r+b') as f:f.seek(1024**2);f.write(os.urandom(65536))
  (live/'new.txt').write_bytes(b'new');(live/'d10/f000010.txt').unlink();(live/'d11/f000011.txt').rename(live/'renamed.txt')
  second=secrets.token_hex(16);snapshots.create(second,qgroup=budget.group);increment_started=time.monotonic();increment=upload(second,first);receive(second);elapsed=time.monotonic()-increment_started
  assert files(snapshots.path(second))==files(target/snapshots.path(second).name);assert old==files(target/snapshots.path(first).name)
  latest=snapshots.metadata(second);assert snapshots.source()['generation']<=latest['creation_generation'],'Idle source must not trigger a new checkpoint'
  print('Accounting before cleanup:',budget.check(),flush=True)
  snapshots.discard(first,snapshots.metadata(first)['snapshot_uuid']);assert snapshots.source()['generation']<=latest['creation_generation'],'Checkpoint cleanup must not trigger upload churn'
  usage=budget.check()
  results.append({'snapshot_old_blocks':usage['old_bytes'],'files':count+1,'baseline_bytes':full['bytes'],'increment_bytes':increment['bytes'],'increment_seconds':elapsed,'baseline_restore_and_resume':'PASS','increment_restore_and_deletion':'PASS','prior_version_preserved':True});print(json.dumps(results[-1]),flush=True)
  # Reset fixture-only names needed by the next fixed-delta round.
  (live/'new.txt').unlink();(live/'renamed.txt').rename(live/'d11/f000011.txt');(live/'d10/f000010.txt').write_bytes(b'restored fixture')
 assert abs(results[0]['increment_bytes']-results[1]['increment_bytes'])<4096
 outcome={'state':'PASS','results':results,'scope':'Two new tmpfs regular-file images; encrypted paged object fixture, interrupted baseline regenerated/resumed, independent receive, no production files/cloud accessed'}
except BaseException as e:outcome={'state':'FAIL','error':str(e),'results':results};raise
finally:
 if journal:journal.close()
 cleanup=[]
 for mount in reversed(mounts[1:]):
  try:run(['umount',mount])
  except Exception as e:cleanup.append(str(e))
 for loop,image in reversed(loops):
  try:assert run(['losetup','-n','-O','BACK-FILE',loop])==str(image);run(['losetup','-d',loop])
  except Exception as e:cleanup.append(str(e))
 if mounts:
  for _ in range(20):
   try:run(['umount',work]);break
   except Exception:time.sleep(.1)
  else:cleanup.append('Fixture tmpfs could not be unmounted')
 if cleanup:outcome.update(state='CLEANUP_FAILED',cleanup_errors=cleanup)
 report.write_text(json.dumps(outcome,indent=2));print('Fixture report:',report,flush=True)
