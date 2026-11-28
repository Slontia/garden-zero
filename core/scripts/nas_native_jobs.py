"""Resource-limited, independently cancellable native backup jobs."""
import json,os,subprocess,time
from pathlib import Path
from nas_stream import identifier
from nas_backup_control import save,operation


def launch(c,accounts,row):
 from nas_storage import specification,device
 spec,_=specification(c);partition=device(spec)
 # Throttle the physical parent, which is the accounting device for partitions.
 parent=Path('/sys/class/block')/partition.name
 disk=Path('/dev')/parent.resolve().parent.name
 unit='nas-native-'+identifier(row['id']);root=Path(c['host'])/'state/native-jobs';root.mkdir(mode=0o700,exist_ok=True)
 specfile=root/(row['id']+'.json')
 if specfile.exists():raise RuntimeError('Backup task identifier already exists')
 save(specfile,{'format':1,'id':row['id'],'accounts':[a['id'] for a in accounts],'created':time.time()})
 args=['systemd-run','--quiet','--collect','--unit='+unit,'--property=CPUQuota=20%','--property=MemoryMax=512M','--property=MemorySwapMax=0','--property=Nice=19','--property=IOWeight=10','--property=IOReadBandwidthMax='+str(disk)+' 4194304','--property=IOWriteBandwidthMax='+str(disk)+' 1048576','--property=UMask=0077','--property=NoNewPrivileges=yes','--property=ProtectSystem=strict','--property=ReadWritePaths='+c['host']+'/state '+c['host']+'/logs '+spec['mount']+' /run/nas/lifecycle.lock','--property=ReadOnlyPaths='+c['data'],'--property=TemporaryFileSystem=/tmp:rw,nosuid,nodev,noexec,size=128m','--property=TimeoutStopSec=30','--setenv=PYTHONDONTWRITEBYTECODE=1','python3','-B',str(Path(c['host'])/'installed/native-backup-worker.py'),'--job',row['id']]
 try:
  subprocess.run(args,check=True,capture_output=True,text=True)
  from nas_backup_control import read,alive
  deadline=time.monotonic()+15
  while time.monotonic()<deadline:
   current=read(Path(c['host'])/'state/backup-operation.json',{})
   if current.get('id')==row['id'] and current.get('pid')!=row['pid'] and (alive(current) or current.get('state') in ('success','failed','interrupted')):return {'state':current['state'],'task':row['id']}
   status=subprocess.run(['systemctl','show',unit+'.service','--property=ActiveState','--value'],capture_output=True,text=True,check=True).stdout.strip()
   if status not in ('active','activating'):raise RuntimeError('Native worker exited before ownership handoff')
   time.sleep(.1)
  raise RuntimeError('Native worker ownership handoff timed out')
 except BaseException:
  # This name was generated for this exact launch, never an application unit.
  subprocess.run(['systemctl','stop',unit+'.service'],capture_output=True,timeout=40)
  specfile.unlink(missing_ok=True)
  with operation(c,'cloud',row):raise RuntimeError('Native backup worker could not start') from None


def handoff(c,ident):
 """Keep the original cancellation ID while moving ownership out of the agent."""
 from nas_backup_control import read
 import fcntl
 root=Path(c['host'])/'state';path=root/'backup-operation.json'
 with (root/'backup-control.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX);row=read(path,{})
  if row.get('id')!=ident or row.get('state') not in ('running','cancelling'):raise RuntimeError('Backup task is no longer owned by this launch')
  row.update(pid=os.getpid(),process_start=Path('/proc/self/stat').read_text().split()[21]);save(path,row)
 return row
