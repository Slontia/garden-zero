#!/usr/bin/env python3
"""Install reviewable NAS-only host files. Check by default; no business restart."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import argparse,datetime,hashlib,json,os,subprocess,sys,tempfile
from pathlib import Path
from nasctl import cfg
TOOLS=['nas_joplin.py','nas_database.py','nasctl.py','nas_ready.py','nas_resources.py','nas_apps.py','nas_access.py','nas_policy.py','soak-check.py','external-backup.py','nas_mirror.py','nas_identity.py','nas_writer.py','nas_sync.py','file-writer-entrypoint.py']
UNITS=['nas-agent.service','nas-session.service','nas-soak.service','nas-soak.timer']

def bundle(c):
 root=Path(c['root']);host=Path(c['host'])
 if c['project']!='nas' or str(host)!='/mnt/c/nas/system' or c['mount']!='/run/nas/database':raise RuntimeError('Only the documented single-instance paths are supported')
 files={Path('/etc/systemd/system')/name:(root/'core/host'/name).read_bytes() for name in UNITS}
 files.update({host/'installed'/name:(root/'core/scripts'/name).read_bytes() for name in TOOLS})
 files[host/'installed/data-access.json']=(root/'core/templates/data-access.json').read_bytes()
 files[host/'config/nas.yml']=(root/'core/compose/nas.yml').read_bytes()
 return files

def review(files):
 result=[]
 for path,data in files.items():
  if path.is_symlink():raise RuntimeError('Installation target is a symlink: '+str(path))
  state='create' if not path.exists() else 'unchanged' if path.read_bytes()==data else 'update'
  result.append({'path':str(path),'action':state,'sha256':hashlib.sha256(data).hexdigest()})
 return result

def stage(c,directory):
 # Staging contains code/unit definitions, never credentials or live state.
 directory=Path(directory).resolve();base=Path(c['host'])/'state'
 if not directory.is_relative_to(base.resolve()) or directory.exists():raise RuntimeError('Staging must be a NEW directory inside host/state')
 directory.mkdir(mode=0o700)
 for path,data in bundle(c).items():
  target=directory/str(path).lstrip('/');target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
 return directory

def main():
 p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');p.add_argument('--apply',action='store_true');p.add_argument('--allow-update',action='store_true');p.add_argument('--stage');a=p.parse_args();c=cfg()
 if a.check and (a.apply or a.stage):raise RuntimeError('--check cannot install, stage or activate services')
 if a.stage:
  if a.apply:raise RuntimeError('Staging cannot install or activate services')
  print('Staged NAS host files: '+str(stage(c,a.stage)));return
 files=bundle(c);rows=review(files)
 if not a.apply:
  print(json.dumps({'mode':'read_only','files':rows,'business_restart':False},indent=2));return
 if os.geteuid()!=0:raise RuntimeError('Requires root')
 if any(r['action']=='update' for r in rows) and not a.allow_update:raise RuntimeError('Existing files differ; inspect --check output, then use --allow-update')
 # Verify unit syntax and dependencies before changing installed files.
 run=lambda args:subprocess.run(args,check=True,stdout=subprocess.DEVNULL)
 with tempfile.TemporaryDirectory(prefix='nas-unit-check-',dir='/run') as tmp:
  units=[]
  for name in UNITS:
   p=Path(tmp)/name;p.write_bytes(files[Path('/etc/systemd/system')/name]);p.chmod(0o644);units.append(str(p))
  run(['systemd-analyze','verify',*units])
 stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');rollback=Path(c['host'])/'state'/('host-install-'+stamp);rollback.mkdir(mode=0o700)
 changed=[]
 try:
  for row in rows:
   if row['action']=='unchanged':continue
   path=Path(row['path']);path.parent.mkdir(parents=True,exist_ok=True)
   if path.exists():
    saved=rollback/str(path).lstrip('/');saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(path.read_bytes())
   fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
   try:
    os.fchmod(fd,0o644)
    with os.fdopen(fd,'wb') as f:f.write(files[path]);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path);changed.append(row)
   finally:
    if Path(tmp).exists():Path(tmp).unlink()
  run(['systemctl','daemon-reload'])
  (rollback/'result.json').write_text(json.dumps({'state':'PASS','files':rows,'business_restart':False},indent=2))
 except Exception:
  for row in reversed(changed):
   path=Path(row['path']);saved=rollback/str(path).lstrip('/')
   if saved.exists():path.write_bytes(saved.read_bytes())
   else:path.unlink()
  run(['systemctl','daemon-reload']);raise
 print('Host files installed; business not restarted. Evidence: '+str(rollback/'result.json'))
if __name__=='__main__':
 try:main()
 except Exception as error:raise SystemExit('Host installation refused: '+str(error))
