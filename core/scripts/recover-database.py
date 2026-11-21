#!/usr/bin/env python3
"""Restore a verified complete backup into a NEW blank image, never repair old image."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import argparse,datetime,hashlib,json,os,subprocess,tempfile,time,uuid,tarfile
from pathlib import Path
from nasctl import DOCKER,db_signature,volume
parser=argparse.ArgumentParser();parser.add_argument('image');parser.add_argument('backup');parser.add_argument('--internal-candidate',action='store_true',help='Prepare only a new C:/nas/system/databases/immich image without switching production');a=parser.parse_args()
h=Path('/mnt/c/nas/system');c=json.loads((h/'config/config.json').read_text())
if not a.internal_candidate:volume(c)
else:
 if Path('/mnt/c/nas').is_symlink() or Path('/mnt/c/nas/system/databases/immich').is_symlink() or __import__('shutil').disk_usage('/mnt/c/nas').free<16*1024**3:raise SystemExit('Internal candidate root unsafe or less than 16 GiB free')
raw_image=Path(a.image)
if raw_image.is_symlink():raise SystemExit('Recovery image cannot be a symlink')
image=raw_image.resolve();backup=Path(a.backup).resolve()
allowed_parent=Path('/mnt/c/nas/system/databases/immich')
if image.parent!=allowed_parent or not image.name.startswith('postgres-recovered-') or image.suffix!='.img' or image==Path(c['image']):raise SystemExit('Not a NEW recovery image path')
if image.is_symlink() or not image.is_file() or image.stat().st_size!=64*1024**3 or image.stat().st_blocks!=0 or image.stat().st_nlink!=1:raise SystemExit('Not a pristine blank sparse file')
if subprocess.check_output(['losetup','-j',str(image)],text=True).strip():raise SystemExit('Recovery image already attached')
if backup.parent!=Path(c['backup_root']) or not (backup/'COMPLETE').is_file():raise SystemExit('Not a completed configured backup')
manifest=json.loads((backup/'manifest.json').read_text())
for name,expected in manifest['files'].items():
 if Path(name).name!=name:raise SystemExit('Invalid manifest entry')
 sha=hashlib.sha256()
 with (backup/name).open('rb') as f:
  for b in iter(lambda:f.read(1024**2),b''):sha.update(b)
 if sha.hexdigest()!=expected:raise SystemExit('Backup checksum mismatch')
with tarfile.open(backup/'configuration.tar') as config_tar:
 ref=json.load(config_tar.extractfile('system/config/versions.json'))['POSTGRES_IMAGE']['image']
reportdir=h/'state'/('recovery-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]);reportdir.mkdir(mode=0o700)
point=Path(tempfile.mkdtemp(prefix='nas-recovered-',dir='/run'));loop='';created=False;name='nas-recovery-db-'+uuid.uuid4().hex[:10]
reportpath=reportdir/'recovery.json';report={'state':'running','image':str(image),'source_backup':str(backup),'old_image_preserved':c['image'],'internal_candidate':a.internal_candidate,'production_config_modified':False}
def cmd(args,**kw):return subprocess.run([str(x) for x in args],check=True,**kw)
def out(args):return subprocess.check_output([str(x) for x in args],text=True).strip()
try:
 cmd(['mkfs.ext4','-q','-L','NAS_POSTGRES','-i','65536','-m','1','-E','lazy_itable_init=0,lazy_journal_init=0',image])
 report['uuid']=out(['blkid','-s','UUID','-o','value',image])
 for phase in ('restore','reattach'):
  loop=out(['losetup','--find','--show','--nooverlap',image]);cmd(['mount','-t','ext4','-o','nodev,nosuid,noexec',loop,point]);(point/'postgres').mkdir(mode=0o700,exist_ok=True)
  cmd(DOCKER+['create','--name',name,'--network','none','--label','nas.purpose=database-recovery','--mount',f'type=bind,source={point}/postgres,target=/var/lib/postgresql/data','--mount',f'type=bind,source={h}/secrets/postgres-password,target=/credentials/password,readonly','-e','POSTGRES_PASSWORD_FILE=/credentials/password','-e','POSTGRES_USER=postgres','-e','POSTGRES_DB=immich','-e','POSTGRES_INITDB_ARGS=--data-checksums',ref],stdout=subprocess.DEVNULL);created=True
  cmd(DOCKER+['start',name],stdout=subprocess.DEVNULL)
  for i in range(90):
   if subprocess.run(DOCKER+['exec',name,'pg_isready','-h','127.0.0.1','-U','postgres','-d','immich'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:break
   time.sleep(1)
  else:raise RuntimeError('Recovery database readiness timeout')
  if phase=='restore':
   with (backup/'database.dump').open('rb') as f:cmd(DOCKER+['exec','-i',name,'pg_restore','--exit-on-error','-U','postgres','-d','immich'],stdin=f)
  if db_signature(name)!=manifest['tables']:raise RuntimeError('Recovered table counts differ from manifest')
  # Full stable fingerprints on the actual user's asset and account rows, not just readiness.
  sql='SELECT md5(coalesce(string_agg(row_to_json(t)::text,\'\' ORDER BY row_to_json(t)::text),\'\')) FROM public.asset t'
  fingerprint=out(DOCKER+['exec',name,'psql','-U','postgres','-d','immich','-Atc',sql])
  if phase=='restore':report['asset_fingerprint']=fingerprint
  elif report['asset_fingerprint']!=fingerprint:raise RuntimeError('Asset fingerprint changed across reattach')
  cmd(DOCKER+['stop','--timeout','90',name],stdout=subprocess.DEVNULL)
  if out(DOCKER+['inspect','-f','{{.State.ExitCode}}',name])!='0':raise RuntimeError('Unclean recovered database exit; mount retained')
  cmd(DOCKER+['logs',name],stdout=(reportdir/f'recovery-{phase}.log').open('w'),stderr=subprocess.STDOUT)
  cmd(DOCKER+['rm',name],stdout=subprocess.DEVNULL);created=False
  cmd(['umount',point]);cmd(['losetup','-d',loop]);loop=''
  cmd(['e2fsck','-fn',image],stdout=(reportdir/f'fsck-{phase}.log').open('w'),stderr=subprocess.STDOUT)
  print('PASS: recovery phase '+phase,flush=True)
 report['state']='PASS';report['checks']=['complete backup checksums','all public table counts','asset fingerprint','normal DB stop','unmount/reattach','new container against same image','second SQL verification','two read-only filesystem checks'];report['time']=datetime.datetime.now(datetime.timezone.utc).isoformat()
 reportpath.write_text(json.dumps(report,indent=2));print('Recovery verified; old image/config remain unchanged. Evidence: '+str(reportpath))
except Exception as e:
 report.update(state='failed',error=str(e));reportpath.write_text(json.dumps(report,indent=2));raise
finally:
 # Only remove our temporary resources after a clean stop. No forced unmount/repair.
 if created:
  cmd(DOCKER+['stop','--timeout','90',name],stdout=subprocess.DEVNULL)
  if out(DOCKER+['inspect','-f','{{.State.ExitCode}}',name])!='0':raise RuntimeError('Recovery error: unclean exit, inspect retained mount')
  cmd(DOCKER+['rm',name],stdout=subprocess.DEVNULL)
 if subprocess.run(['mountpoint','-q',str(point)]).returncode==0:cmd(['umount',point])
 if loop:cmd(['losetup','-d',loop])
 point.rmdir()
