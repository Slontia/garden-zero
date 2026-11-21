#!/usr/bin/env python3
"""Encrypted versioned offsite backup adapter. Never formats a disk or prunes automatically."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import argparse,datetime,json,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('target',choices=['cloud','hdd']);p.add_argument('operation',choices=['init','backup','check','snapshots']);a=p.parse_args()
c=json.loads(Path('/mnt/c/nas/system/config/config.json').read_text());host=Path(c['host']);state=host/'state'/(a.target+'-backup.json')
repo=c.get('cloud_repository') if a.target=='cloud' else c.get('hdd_root')
if not repo:raise SystemExit(a.target+' is not configured; no backup was executed')
image=json.loads((Path(c['host'])/'config/versions.json').read_text())['RESTIC_IMAGE']['image']
d=['/usr/bin/docker','--host','unix:///var/run/docker.sock','run','--rm','--label','nas.purpose=external-backup','--read-only','--tmpfs','/tmp:size=128m','--mount',f'type=bind,source={host}/secrets/backup-password,target=/credentials/password,readonly','-e','RESTIC_PASSWORD_FILE=/credentials/password']
if a.target=='cloud':
 if not repo.startswith(('s3:','sftp:','rest:','b2:','azure:','rclone:')):raise SystemExit('Expected explicit supported remote repository; refusing local-path cloud backup')
 credentials=host/'secrets/cloud.env'
 if not credentials.is_file():raise SystemExit('Cloud credentials not configured')
 d+=['--env-file',str(credentials),'-e','RESTIC_REPOSITORY='+repo]
else:
 root=Path(repo)
 if not root.is_dir() or root.is_symlink() or not str(root).startswith('/mnt/'):raise SystemExit('HDD target missing/invalid')
 drive=str(root).split('/')[2]
 if len(drive)!=1 or drive.lower() in ('c','d') or not c.get('hdd_guid'):raise SystemExit('Independent HDD identity required; C: and NAS SSD refused')
 ps='/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe'
 volume=json.loads(subprocess.check_output([ps,'-NoProfile','-NonInteractive','-Command',f'Get-Volume -DriveLetter {drive} | Select-Object UniqueId | ConvertTo-Json -Compress'],text=True))
 if c['hdd_guid'].lower() not in volume['UniqueId'].lower():raise SystemExit('Wrong HDD identity')
 d+=['--mount',f'type=bind,source={root},target=/repository','-e','RESTIC_REPOSITORY=/repository']
if a.operation=='backup':
 completed=sorted(x for x in Path(c['backup_root']).iterdir() if x.is_dir() and (x/'COMPLETE').is_file())
 if not completed:raise SystemExit('No COMPLETE consistent backup set to copy')
 latest=completed[-1]
 d+=['--mount',f'type=bind,source={latest},target=/backup-set,readonly',image,'backup','--tag','nas','/backup-set']
else:d+=[image,a.operation]
try:
 subprocess.run(d,check=True)
 state.write_text(json.dumps({'state':'success','operation':a.operation,'time':datetime.datetime.now(datetime.timezone.utc).isoformat()}))
except Exception:
 state.write_text(json.dumps({'state':'failed','operation':a.operation}));raise
