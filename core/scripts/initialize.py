#!/usr/bin/env python3
"""Initialize only a prepared, new image file; existing instances are never overwritten."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import argparse,fcntl,hashlib,json,os,re,secrets,subprocess,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
HOST=Path('/mnt/c/nas/system')
INTERNAL_ROOT=Path('/mnt/c/nas/system/databases/immich')

def plan(host=HOST):
 host=Path(host);config=host/'config/config.json';image=INTERNAL_ROOT/'postgres.img'
 if config.exists() or config.is_symlink():raise RuntimeError('Configuration exists; refusing overwrite')
 if host.resolve()!=HOST or host.is_symlink():raise RuntimeError('Only the prepared C:\\nas\\system directory is supported')
 receipt=json.loads((host/'state/prepared-host.json').read_text())
 image=Path(receipt.get('image_wsl',str(image)))
 if image!=INTERNAL_ROOT/'postgres.img':raise RuntimeError('Preparation names an unsupported image target')
 if image.parent==INTERNAL_ROOT and (INTERNAL_ROOT.is_symlink() or INTERNAL_ROOT.parent.is_symlink() or shutil.disk_usage(INTERNAL_ROOT).free<24*1024**3):raise RuntimeError('Internal database directory unsafe or needs at least 24 GiB free')
 if receipt.get('schema')!=2 or receipt.get('filesystem')!='NTFS':raise RuntimeError('Invalid Windows preparation receipt')
 drive=receipt['drive'].lower()
 if len(drive)!=1 or not drive.isalpha() or drive=='c':raise RuntimeError('NAS source must be on a separate Windows drive')
 if not str(ROOT).startswith('/mnt/'+drive+'/'):raise RuntimeError('Checkout and prepared Windows drive do not match')
 winroot=subprocess.check_output(['/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe','-NoProfile','-NonInteractive','-Command',"[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-Volume -DriveLetter '"+drive+"' | Select-Object UniqueId,FileSystem | ConvertTo-Json -Compress"],text=True)
 volume=json.loads(winroot)
 guid=receipt['volume_guid'].lower()
 actual=volume['UniqueId'].split('{')[-1].split('}')[0].lower()
 if actual!=guid or volume['FileSystem']!='NTFS':raise RuntimeError('Prepared Windows volume identity changed')
 if receipt.get('root_wsl')!=str(ROOT):raise RuntimeError('Checkout does not match Windows preparation receipt')
 for p in (ROOT/'system',image.parent,image):
  if p.is_symlink():raise RuntimeError('Symlink storage target refused')
 st=image.stat()
 if not image.is_file() or st.st_size!=64*1024**3 or st.st_blocks!=0 or st.st_nlink!=1:
  raise RuntimeError('Expected NEW zero-allocation single-link 64 GiB regular image; refusing format')
 # DrvFS allocation metadata can lag behind a completed write; ask NTFS too.
 winimage=subprocess.check_output(['wslpath','-w',str(image)],text=True).strip()
 ranges=subprocess.run(['/mnt/c/WINDOWS/System32/fsutil.exe','sparse','queryrange',winimage],capture_output=True)
 if ranges.returncode or re.search(rb'0x[0-9a-fA-F]+',ranges.stdout):raise RuntimeError('NTFS image has allocated ranges or could not be verified; refusing format')
 if subprocess.check_output(['losetup','-j',str(image)],text=True).strip():raise RuntimeError('Image attached')
 secrets_path=host/'secrets'
 if secrets_path.is_symlink() or (secrets_path.exists() and any(secrets_path.iterdir())):raise RuntimeError('Existing secrets found; refusing new initialization')
 for path in (ROOT/'system/config/host.json',host/'state/initialization-started.json'):
  if path.exists() or path.is_symlink():raise RuntimeError('Existing initialization state found; inspect before retry')
 return {'schema':1,'project':'nas','root':str(ROOT),'core':str(ROOT/'core'),'system':str(ROOT/'system'),'host':str(host),'backup_root':'/mnt/c/nas/system/backups','image':str(image),'image_gib':64,'database_location':'internal' if image.parent==INTERNAL_ROOT else 'ssd_legacy','drive':drive,'volume_guid':guid,'actions':['format only the verified NEW image file','create new secrets and configuration'],'physical_disk_format':False}

def initialize(p):
 host=Path(p['host']);image=Path(p['image']);config=host/'config/config.json'
 # Persist an exclusive marker before formatting. Failed attempts require inspection,
 # and a second invocation cannot silently create a different set of credentials.
 marker=host/'state/initialization-started.json'
 with marker.open('x') as f:json.dump({'root':str(ROOT),'image':str(image)},f)
 subprocess.run(['mkfs.ext4','-q','-L','NAS_POSTGRES','-i','65536','-m','1','-E','lazy_itable_init=0,lazy_journal_init=0',str(image)],check=True)
 fs_uuid=subprocess.check_output(['blkid','-s','UUID','-o','value',str(image)],text=True).strip()
 secret_dir=host/'secrets';secret_dir.mkdir(exist_ok=True)
 password=secrets.token_urlsafe(24);salt=secrets.token_bytes(16)
 admin={'username':'admin','salt':salt.hex(),'hash':hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1).hex()}
 values={'admin.json':json.dumps(admin),'admin-password.txt':password+'\n','agent-token':secrets.token_hex(32),'postgres-password':secrets.token_hex(32),'backup-password':secrets.token_hex(32),'smb-password':secrets.token_hex(24)}
 for name,value in values.items():
  fd=os.open(secret_dir/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'w') as f:f.write(value)
 c={'schema':1,'project':'nas','root':str(ROOT),'core':str(ROOT/'core'),'system':str(ROOT/'system'),'host':str(host),'backup_root':p['backup_root'],'image':str(image),'image_uuid':fs_uuid,'database_location':p['database_location'],'critical_image_outer_free':16*1024**3,'mount':'/run/nas/database','volume_guid':p['volume_guid'],'volume_drive':p['drive'],'data':str(ROOT/'data'),'enabled':['immich','files','knowledge','git','smb','syncthing','tailscale','meta','backup','cloud','hdd','ml'],'autostart':['meta','files','knowledge','immich','git','smb','syncthing','tailscale','ml'],'ml_enabled':True,'ports':{'meta':8080,'files':8081,'knowledge':8082,'immich':2283,'git':2222,'syncthing':8384,'smb':1445},'backup_hour':3,'backup_keep':14,'critical_outer_free':16*1024**3,'cloud_repository':None,'hdd_root':None,'sync_target':'mac','sync_folders_pending':True,'file_access':{'mode':'full_sync','write_roots':['shared'],'writer_selection':'disabled','implemented':True}}
 for path in ['logs','runtime','backups','installed','config','state/meta','state/syncthing','state/tailscale','state/git','state/smb','cache/ml','state/cloud']:(host/path).mkdir(parents=True,exist_ok=True)
 for path in ['immich','git','joplin','shared']:(ROOT/'data'/path).mkdir(parents=True,exist_ok=True)
 (host/'config').mkdir(parents=True,exist_ok=True)
 with config.open('x') as f:json.dump(c,f,indent=2)

 marker.rename(host/'state/initialization-completed.json')
 print('Fresh image/configuration created. Credentials stored locally; no passwords printed.')

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--dry-run',action='store_true',help='read-only validation; never formats or writes');a=parser.parse_args()
 if os.geteuid()!=0:raise RuntimeError('Requires root in Docker host mount namespace')
 # flock the existing host directory: no lockfile is created by dry-run.
 fd=os.open(HOST,os.O_RDONLY|os.O_DIRECTORY)
 try:
  fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
  p=plan()
  if a.dry_run:print(json.dumps(p,indent=2));return
  initialize(p)
 finally:os.close(fd)
if __name__=='__main__':
 try:main()
 except Exception as error:raise SystemExit('Initialization refused: '+str(error))
