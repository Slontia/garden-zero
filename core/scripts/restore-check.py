#!/usr/bin/env python3
"""Verify a COMPLETE backup and restore its DB into a new internal-disk ext4 image."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import datetime,hashlib,json,os,subprocess,sys,tarfile,tempfile
from nasctl import db_signature
from pathlib import Path
c=json.loads(Path('/mnt/c/nas/system/config/config.json').read_text());base=Path(c['backup_root'])
if len(sys.argv)!=2:raise SystemExit('Usage: restore-check.py <complete-backup-directory>')
backup=Path(sys.argv[1]).resolve()
if backup.parent!=base.resolve() or not (backup/'COMPLETE').is_file():raise SystemExit('Expected completed internal backup')
manifest=json.loads((backup/'manifest.json').read_text())
for name,expected in manifest['files'].items():
 if Path(name).name!=name:raise SystemExit('Invalid manifest path')
 h=hashlib.sha256()
 with (backup/name).open('rb') as f:
  for b in iter(lambda:f.read(1024**2),b''):h.update(b)
 if h.hexdigest()!=expected:raise SystemExit('Checksum mismatch: '+name)
work=base/('restore-test-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'));work.mkdir()
image=work/'database.img'
with image.open('xb') as f:f.truncate(2*1024**3)
subprocess.run(['mkfs.ext4','-q','-E','lazy_itable_init=0,lazy_journal_init=0',str(image)],check=True)
point=Path(tempfile.mkdtemp(prefix='nas-restore-',dir='/run'));loop='';created=False
D=['/usr/bin/docker','--host','unix:///var/run/docker.sock'];name='nas-restore-check'
with tarfile.open(backup/'configuration.tar') as config_tar:
 ref=json.load(config_tar.extractfile('system/config/versions.json'))['POSTGRES_IMAGE']['image']
def command(args,**kwargs):return subprocess.run(args,check=True,**kwargs)
def capture(args):return subprocess.check_output(args,text=True).strip()
try:
 loop=capture(['losetup','--find','--show','--nooverlap',str(image)])
 command(['mount','-t','ext4','-o','nodev,nosuid,noexec',loop,str(point)])
 (point/'postgres').mkdir(mode=0o700)
 command(D+['create','--name',name,'--label','nas.purpose=restore-check','--network','none','--mount',f'type=bind,source={point}/postgres,target=/var/lib/postgresql/data','-e','POSTGRES_HOST_AUTH_METHOD=trust','-e','POSTGRES_USER=postgres','-e','POSTGRES_DB=immich',ref],stdout=subprocess.DEVNULL);created=True
 command(D+['start',name],stdout=subprocess.DEVNULL)
 import time
 for i in range(90):
  if subprocess.run(D+['exec',name,'pg_isready','-h','127.0.0.1','-U','postgres','-d','immich'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:break
  time.sleep(1)
 else:raise RuntimeError('Restore DB readiness timeout')
 with (backup/'database.dump').open('rb') as f:command(D+['exec','-i',name,'pg_restore','--exit-on-error','-U','postgres','-d','immich'],stdin=f)
 query="SELECT table_schema||'.'||table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY 1"
 restored=capture(D+['exec',name,'psql','-U','postgres','-d','immich','-Atc',query])
 if db_signature(name)!=manifest.get('tables'):raise RuntimeError('Restored table counts do not match backup')
 with tarfile.open(backup/'user-content.tar') as tar:tar.extractall(work/'content',filter='data')
 with tarfile.open(backup/'configuration.tar') as tar:tar.extractall(work/'configuration',filter='data')
 report={'backup':str(backup),'restore_image':str(image),'database_tables':restored.splitlines(),'table_counts_match':True,'state':'PASS','checks':['archive checksums','independent internal-disk database restore','safe user-content extraction','configuration extraction'],'limitations':['Not cross-host disaster recovery','User-content application relations require media upload acceptance']}
 (work/'result.json').write_text(json.dumps(report,indent=2));print('PASS: backup checksums and independent restore. Evidence: '+str(work/'result.json'))
finally:
 if created:
  command(D+['stop','--timeout','60',name],stdout=subprocess.DEVNULL)
  if capture(D+['inspect','-f','{{.State.ExitCode}}',name])!='0':raise RuntimeError('Unclean restore DB stop; mount retained')
  command(D+['rm',name],stdout=subprocess.DEVNULL)
 if subprocess.run(['mountpoint','-q',str(point)]).returncode==0:command(['umount',str(point)])
 if loop:command(['losetup','-d',loop])
 point.rmdir()
 command(['e2fsck','-fn',str(image)],stdout=subprocess.DEVNULL)
