#!/usr/bin/env python3
"""Cloud versioned backup and authenticated incremental local mirror."""
import sys
sys.dont_write_bytecode=True
import argparse,datetime,fcntl,json,os,re,secrets,subprocess
from pathlib import Path
from nas_mirror import Mirror,Snapshot,unsafe,atomic
PS='/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe'

def mirror_target(c):
 root=Path(c['hdd_root']);parts=root.parts
 if len(parts)!=4 or parts[:2]!=('/','mnt') or len(parts[2])!=1 or root.is_symlink() or root.is_junction():raise RuntimeError('Expected a dedicated real /mnt/<drive>/<folder> mirror directory')
 drive=parts[2].lower();source_drive=Path(c['root']).parts[2]
 if drive in ('c',source_drive) or not c.get('hdd_guid') or root.name.casefold()!='nas-backup':raise RuntimeError('Unsafe mirror target; separate configured nas-backup volume required')
 code="[Console]::OutputEncoding=[Text.Encoding]::UTF8; $v=Get-Volume -DriveLetter '"+drive+"' -ErrorAction Stop; $p=Get-Item -LiteralPath '"+drive+":\\nas-backup' -Force -ErrorAction Stop; @{guid=$v.UniqueId;filesystem=$v.FileSystem;attributes=[int]$p.Attributes;container=$p.PSIsContainer}|ConvertTo-Json -Compress"
 result=json.loads(subprocess.check_output([PS,'-NoProfile','-NonInteractive','-Command',code],text=True))
 if c['hdd_guid'].lower() not in result['guid'].lower() or result['filesystem']!='NTFS' or not result['container'] or result['attributes']&1024:raise RuntimeError('Wrong mirror volume identity/filesystem or linked target')
 mount=Path('/mnt')/drive
 # A drive absent during WSL boot may not have been automounted. Never create a fallback target.
 mounted=subprocess.run(['mountpoint','-q',str(mount)]).returncode==0
 if not mounted:
  if os.geteuid()!=0:raise RuntimeError('Mirror drive is not mounted')
  mount.mkdir(exist_ok=True)
  if list(mount.iterdir()):raise RuntimeError('Non-empty unmounted drive path')
  subprocess.run(['mount','-t','drvfs',drive.upper()+':',str(mount)],check=True)
 source=subprocess.check_output(['findmnt','-n','-o','SOURCE','--target',str(root)],text=True).strip()
 if not source.casefold().startswith(drive+':') or not root.is_dir():raise RuntimeError('Mirror path is not on its configured Windows drive')
 keypath=Path(c['host'])/'secrets/hdd-mirror-key'
 checkpoint=Path(c['host'])/'state/hdd-mirror.json'
 if not keypath.exists():
  if list(root.iterdir()) or checkpoint.exists():raise RuntimeError('Cannot create a new signing key for an existing target')
  fd=os.open(keypath,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'wb') as stream:stream.write(secrets.token_bytes(32));stream.flush();os.fsync(stream.fileno())
 if unsafe(keypath) or len(keypath.read_bytes())!=32:raise RuntimeError('Invalid mirror signing key')
 identity={'root':str(root),'volume_guid':c['hdd_guid'].lower(),'nas_root':c['root'],'nas_volume_guid':c['volume_guid'].lower()}
 return Mirror(root,checkpoint,keypath.read_bytes(),identity)

def hdd(c,operation,purge_history=False):
 mirror=mirror_target(c)
 with open(Path(c['host'])/'state/hdd-mirror.lock','a') as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise RuntimeError('Another mirror operation is running')
  if operation in ('check','init','snapshots'):
   result=mirror.check()
  else:
   completed=sorted(p for p in Path(c['backup_root']).iterdir() if re.fullmatch(r'\d{8}-\d{6}',p.name) and p.is_dir() and (p/'COMPLETE').is_file())
   if not completed:raise RuntimeError('No COMPLETE consistent backup available')
   status=mirror.check();pending=status['pending_source']
   if pending:
    if not re.fullmatch(r'\d{8}-\d{6}',pending):raise RuntimeError('Invalid pending source snapshot')
    with Snapshot(Path(c['backup_root'])/pending) as source:mirror.sync(source)
   with Snapshot(completed[-1]) as source:result=mirror.sync(source,purge_history=purge_history)
  win=subprocess.check_output(['wslpath','-w',str(mirror.meta)],text=True).strip()
  subprocess.run(['/mnt/c/WINDOWS/System32/attrib.exe','+H',win],check=True,stdout=subprocess.DEVNULL)
  return result

def cloud(c,operation):
 host=Path(c['host']);repo=c.get('cloud_repository')
 if not repo or not repo.startswith(('s3:','sftp:','rest:','b2:','azure:','rclone:')):raise RuntimeError('Explicit supported remote cloud repository required')
 credentials=host/'secrets/cloud.env'
 if not credentials.is_file():raise RuntimeError('Cloud credentials not configured')
 image=json.loads((host/'config/versions.json').read_text())['RESTIC_IMAGE']['image']
 command=['/usr/bin/docker','--host','unix:///var/run/docker.sock','run','--rm','--label','nas.purpose=external-backup','--read-only','--tmpfs','/tmp:size=128m','--mount',f'type=bind,source={host}/secrets/backup-password,target=/credentials/password,readonly','-e','RESTIC_PASSWORD_FILE=/credentials/password','--env-file',str(credentials),'-e','RESTIC_REPOSITORY='+repo]
 if operation=='backup':
  completed=sorted(p for p in Path(c['backup_root']).iterdir() if re.fullmatch(r'\d{8}-\d{6}',p.name) and p.is_dir() and (p/'COMPLETE').is_file())
  if not completed:raise RuntimeError('No COMPLETE consistent backup set')
  command+=['--mount',f'type=bind,source={completed[-1]},target=/backup-set,readonly',image,'backup','--tag','nas','/backup-set']
 else:command+=[image,operation]
 subprocess.run(command,check=True)
 return {'mode':'encrypted_versioned_repository'}

def main():
 parser=argparse.ArgumentParser();parser.add_argument('target',choices=['cloud','hdd']);parser.add_argument('operation',choices=['init','backup','check','snapshots']);parser.add_argument('--purge-history',action='store_true');args=parser.parse_args()
 if args.purge_history and (args.target!='hdd' or args.operation!='backup'):raise RuntimeError('--purge-history is only valid for an HDD backup')
 c=json.loads(Path('/mnt/c/nas/system/config/config.json').read_text());state=Path(c['host'])/'state'/(args.target+'-backup.json')
 previous=json.loads(state.read_text()) if state.exists() else {};record={'state':'running','operation':args.operation,'started':datetime.datetime.now(datetime.timezone.utc).isoformat(),'last_success':previous.get('last_success')}
 atomic(state,record)
 try:
  result=hdd(c,args.operation,args.purge_history) if args.target=='hdd' else cloud(c,args.operation)
  now=datetime.datetime.now(datetime.timezone.utc).isoformat();record.update(state='interrupted' if result.get('pending_source') else 'success',time=now,**result)
  if args.operation=='backup':record['last_success']=now
  atomic(state,record);print(json.dumps(record,ensure_ascii=False))
 except Exception as error:
  record.update(state='failed',error=str(error));atomic(state,record);raise
if __name__=='__main__':
 try:main()
 except Exception as error:raise SystemExit('External backup refused: '+str(error))
