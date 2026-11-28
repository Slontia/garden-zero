#!/usr/bin/env python3
"""Manual experiment only: new tmpfs-backed image files, never production data."""
import os,sys,json,time,hashlib,subprocess,stat,datetime,argparse
from pathlib import Path
parser=argparse.ArgumentParser(description='Opt-in isolated Btrfs stream/restore evaluation. Run inside a private mount namespace and resource-limited unit.')
parser.add_argument('--run',action='store_true');parser.add_argument('--workspace',type=Path,required=True);parser.add_argument('--tools-dir',type=Path,required=True);parser.add_argument('--report',type=Path,required=True)
args=parser.parse_args()
if not args.run:raise SystemExit('Read this script before opting in with --run; two new regular-file images are formatted, never physical disks.')
BASE=args.workspace.resolve();WORK=BASE/'fixture';REPORT=args.report.resolve()
assert BASE.parent==Path('/run/nas') and BASE.name.startswith('storage-eval-') and BASE.is_dir()
assert not REPORT.is_relative_to(WORK) and not REPORT.exists(),'Report must be new and outside disposable mounts'
BIN=args.tools_dir.resolve()/'usr/bin/btrfs';MKFS=args.tools_dir.resolve()/'usr/sbin/mkfs.btrfs'
if not MKFS.exists():MKFS=args.tools_dir.resolve()/'usr/bin/mkfs.btrfs'
assert os.geteuid()==0 and BASE.parent==Path('/run/nas') and not BASE.is_symlink()
assert os.readlink('/proc/self/ns/mnt')!=os.readlink('/proc/1/ns/mnt'),'Private mount namespace required'
WORK.mkdir(exist_ok=False);loops=[];mounted=[];results=[]
def run(args):return subprocess.check_output([str(x) for x in args],stderr=subprocess.STDOUT,text=True)
def allocated(p):return p.stat().st_blocks*512
def snapshot(live,name):
 t=time.monotonic();point=live.parent/name;run([BIN,'subvolume','snapshot','-r',live,point]);return point,time.monotonic()-t
def transfer(point,destination,parent=None):
 args=[str(BIN),'send']+(['-p',str(parent)] if parent else [])+[str(point)]
 t=time.monotonic();count=0;digest=hashlib.sha256()
 with subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE) as send,subprocess.Popen([str(BIN),'receive',str(destination)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE) as receive:
  while True:
   block=send.stdout.read(65536)
   if not block:break
   digest.update(block);count+=len(block);receive.stdin.write(block)
  receive.stdin.close();code=receive.wait(timeout=60);sendcode=send.wait(timeout=60)
  if code or sendcode:raise RuntimeError('Send/receive failed: '+receive.stderr.read().decode()+send.stderr.read().decode())
 return {'bytes':count,'seconds':time.monotonic()-t,'sha256':digest.hexdigest()}
def inventory(root):
 return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
def populate(live,start,stop):
 for i in range(start,stop):
  d=live/('d%02d'%(i%32));d.mkdir(exist_ok=True);(d/('f%06d'%i)).write_bytes(b'initial:'+str(i).encode()+b'x'*96)
def modify(live,phase):
 for i in range(10):(live/('d%02d'%(i%32))/('f%06d'%i)).write_bytes(b'modified:'+bytes([phase])*96)
 old=live/('d%02d'%((100+phase)%32))/('f%06d'%(100+phase))
 old.unlink();p=live/('d%02d'%((200+phase)%32))/('f%06d'%(200+phase));p.rename(p.with_name('renamed-'+str(phase)))
 (live/('new-'+str(phase))).write_bytes(b'new content')
 with (live/'large.bin').open('r+b') as stream:stream.seek(1024*1024);stream.write(bytes([phase])*65536);stream.flush();os.fsync(stream.fileno())
try:
 run(['mount','-t','tmpfs','-o','size=256m,nosuid,nodev','nas-storage-eval',WORK]);mounted.append(WORK)
 run(['modprobe','btrfs'])
 for name in ['source','restore']:
  image=WORK/(name+'.img')
  fd=os.open(image,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600);os.ftruncate(fd,512*1024*1024);os.close(fd)
  assert stat.S_ISREG(image.stat().st_mode)
  print('Creating disposable regular-file Btrfs image:',name,flush=True)
  run([MKFS,'-q','--nodiscard','-d','single','-m','single',image])
  loop=run(['losetup','--find','--show',image]).strip();loops.append((loop,image))
  target=WORK/name;target.mkdir();run(['mount','-t','btrfs','-o','noatime',loop,target]);mounted.append(target)
 source=WORK/'source';restore=WORK/'restore';live=source/'live';run([BIN,'subvolume','create',live])
 populate(live,0,1000);(live/'large.bin').write_bytes(os.urandom(8*1024*1024));run([BIN,'filesystem','sync',source])
 for phase,count in [(1,1000),(2,10000)]:
  if phase==2:populate(live,1000,10000)
  run([BIN,'filesystem','sync',source]);before=allocated(WORK/'source.img')
  baseline,snap_time=snapshot(live,'base-'+str(count));run([BIN,'filesystem','sync',source]);after=allocated(WORK/'source.img')
  full=transfer(baseline,restore)
  assert inventory(baseline)==inventory(restore/baseline.name)
  modify(live,phase);point,point_time=snapshot(live,'increment-'+str(count));increment=transfer(point,restore,parent=baseline)
  assert inventory(point)==inventory(restore/point.name)
  assert inventory(baseline)==inventory(restore/baseline.name),'Previous recovery point changed'
  shared=run([BIN,'filesystem','du','-s','--raw',live/'large.bin'])
  result={'files_before_change':count+1,'fixed_changes':'10 tiny-file edits + one 64KiB range overwrite + create/delete/rename', 'baseline_stream':full,'increment_stream':increment,'baseline_snapshot_seconds':snap_time,'increment_snapshot_seconds':point_time,'image_allocated_before_snapshot':before,'image_allocated_after_snapshot':after,'large_file_extents':shared,'full_and_incremental_restore_sha256':'PASS','previous_recovery_point_preserved':True}
  results.append(result);print(json.dumps(result),flush=True)
 report={'state':'PASS','time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'private mount namespace, 256MiB tmpfs, two fresh 512MiB sparse regular-file images; no production data or physical disks formatted','limits':{'tmpfs':'256MiB','external_resource_limits':'Set by invoking systemd unit; not inferred by this script'},'results':results,'not_validated':['NTFS/DrvFS and USB durability','Aliyun cloud transport or multipart resume','large-scale performance','application compatibility and database transaction consistency']}
except BaseException as error:
 report={'state':'FAIL','error':str(error),'results':results};raise
finally:
 cleanup=[]
 for target in reversed(mounted[1:]):
  try:run(['umount',target])
  except Exception as e:cleanup.append(str(e))
 for loop,image in reversed(loops):
  try:
   backing=run(['losetup','--noheadings','--output','BACK-FILE',loop]).strip()
   assert backing==str(image),'Refuse unrelated loop detach'
   run(['losetup','-d',loop])
  except Exception as e:cleanup.append(str(e))
 if WORK in mounted:
  for attempt in range(20):
   try:run(['umount',WORK]);break
   except Exception as error:
    if attempt==19:cleanup.append(str(error))
    else:time.sleep(.1)
 if cleanup:report['state']='CLEANUP_FAILED'
 report['cleanup_errors']=cleanup;REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2));print('Report:',REPORT,flush=True)
