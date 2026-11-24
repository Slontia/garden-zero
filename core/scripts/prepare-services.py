#!/usr/bin/env python3
"""Prepare missing app settings safely; never overwrite existing credentials/identity."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import argparse,json,os,secrets,subprocess,sys,xml.etree.ElementTree as ET
from pathlib import Path
from nasctl import cfg,DOCKER

def write_new(path,data,mode=0o600):
 path=Path(path);data=data.encode() if isinstance(data,str) else data
 if path.is_symlink():raise RuntimeError('Symlink target refused: '+path.name)
 if path.exists():
  if path.read_bytes()!=data:raise RuntimeError('Existing settings differ; explicit migration required: '+path.name)
  return False
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
 with os.fdopen(fd,'wb') as f:f.write(data)
 return True

def validate_syncthing(directory):
 directory=Path(directory)
 if directory.is_symlink():raise RuntimeError('Syncthing state cannot be a symlink')
 root=ET.parse(directory/'config.xml').getroot();gui=root.find('gui')
 if gui is None or gui.findtext('user')!='admin' or not (gui.findtext('password') or '').startswith(('$2a$','$2b$','$2y$')):raise RuntimeError('Syncthing GUI authentication missing')
 for folder in root.findall('folder'):
  params={p.get('key'):p.get('val') for p in folder.findall('versioning/param')}
  if folder.get('id')!='nas-shared' or folder.get('path')!='/var/syncthing/Shared' or folder.get('type')!='sendreceive' or folder.findtext('maxConflicts')!='-1' or folder.find('versioning') is None or folder.find('versioning').get('type')!='simple' or params.get('keep')!='20':raise RuntimeError('Unrecognized sync folders; explicit migration required')
 if gui.findtext('address')!='0.0.0.0:8384':raise RuntimeError('Unexpected Syncthing GUI bind address')
 for name in ['cert.pem','key.pem']:
  p=directory/name
  if not p.is_file() or p.is_symlink():raise RuntimeError('Syncthing identity missing or symlink')
 return {'authentication':'bcrypt','folders':len(root.findall('folder')),'identity':'preserved'}

def prepare_syncthing(c,versions):
 host=Path(c['host']);target=host/'state/syncthing'
 if (target/'config.xml').exists():return validate_syncthing(target)
 if target.exists() and (target.is_symlink() or any(target.iterdir())):raise RuntimeError('Partial Syncthing state exists; inspect before retry')
 stage=host/'state'/('.syncthing-prepare-'+secrets.token_hex(6));stage.mkdir(mode=0o700)
 password=(host/'secrets/admin-password.txt').read_text().strip()
 command=DOCKER+['run','--rm','-i','--network','none','--read-only','--tmpfs','/tmp:size=16m','--user','0:0','--mount',f'type=bind,source={stage},target=/generated','--entrypoint','/bin/syncthing',versions['SYNCTHING_IMAGE']['image'],'generate','--home=/generated','--gui-user=admin','--gui-password=-','--no-port-probing']
 # Password via stdin, never Docker CLI arguments or logs. Generation starts no daemon.
 result=subprocess.run(command,input=password+'\n',text=True,capture_output=True)
 if result.returncode:raise RuntimeError('Syncthing generation failed; staged identity retained for inspection')
 xml=stage/'config.xml';tree=ET.parse(xml);root=tree.getroot()
 for folder in root.findall('folder'):root.remove(folder)
 root.find('gui/address').text='0.0.0.0:8384'
 tree.write(xml,encoding='utf-8',xml_declaration=True)
 validate_syncthing(stage)
 if target.exists():target.rmdir()  # Only the verified empty target; never remove old state.
 stage.rename(target)
 return {'authentication':'bcrypt','folders':0,'identity':'new'}

def prepare(c,versions):
 host=Path(c['host']);required=['admin-password.txt','postgres-password','smb-password','agent-token','admin.json']
 for name in required:
  p=host/'secrets'/name
  if not p.is_file() or p.is_symlink():raise RuntimeError('Required local credential missing: '+name)
 # Validate every existing immutable app setting before writing any missing files.
 from nas_database import credentials
 credentials(c)
 values={host/'config/immich.json':json.dumps({'machineLearning':{'enabled':bool(c['ml_enabled']),'urls':['http://immich-machine-learning:3003']}},indent=2)+'\n'}
 for path,data in values.items():
  if path.is_symlink():raise RuntimeError('Settings symlink refused')
  if path.exists():
   same=all(json.loads(path.read_text()).get(key)==value for key,value in json.loads(data).items()) if path.suffix=='.json' else path.read_text().strip()==data.strip()
   if not same:raise RuntimeError('Existing app settings differ; automatic overwrite refused: '+path.name)
 for path,data in values.items():
  if not path.exists():write_new(path,data)
 keys=host/'state/git/authorized_keys';keys.parent.mkdir(parents=True,exist_ok=True)
 if keys.is_symlink():raise RuntimeError('Git authorized_keys symlink refused')
 if not keys.exists():write_new(keys,'',0o600)
 syn=prepare_syncthing(c,versions)
 return {'immich':'prepared','git':'configured' if keys.read_text().strip() else 'awaiting_public_key','syncthing':syn}

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true');a=parser.parse_args();c=cfg()
 versions=json.loads((Path(c['host'])/'config/versions.json').read_text())
 if a.check:
  print(json.dumps({'syncthing':validate_syncthing(Path(c['host'])/'state/syncthing'),'mode':'read_only'}));return
 if os.geteuid()!=0:raise RuntimeError('Requires root in Docker host mount namespace')
 print(json.dumps(prepare(c,versions)))
if __name__=='__main__':
 try:main()
 except Exception as error:raise SystemExit('Service preparation refused: '+str(error))
