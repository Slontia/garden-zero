#!/usr/bin/env python3
"""Exercise fresh service prep in private fixtures, with no production ports or data."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import base64,datetime,hashlib,importlib.util,json,secrets,subprocess,sys,time,uuid,xml.etree.ElementTree as ET
from pathlib import Path
from nasctl import cfg,DOCKER

def load(name):
 p=Path(__file__).resolve().parents[1]/'scripts'/(name+'.py');spec=importlib.util.spec_from_file_location(name.replace('-','_'),p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def fingerprint(c):
 paths=[Path(c['host'])/'config/config.json',Path(c['host'])/'config/compose.env']+list((Path(c['host'])/'secrets').glob('*'))
 return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}

def main():
 c=cfg();before=fingerprint(c);stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6];work=Path(c['host'])/'state'/('prepare-check-'+stamp);work.mkdir(mode=0o700)
 host=work/'host';root=work/'source';(host/'secrets').mkdir(parents=True);(host/'state/git').mkdir(parents=True);(root/'core/templates').mkdir(parents=True);(root/'system/config').mkdir(parents=True);(host/'config').mkdir();(root/'data').mkdir()
 (root/'core/templates/versions.lock.json').write_bytes((Path(c['root'])/'core/templates/versions.lock.json').read_bytes())
 password=secrets.token_urlsafe(24)
 for name,value in {'admin-password.txt':password+'\n','postgres-password':secrets.token_hex(32),'smb-password':secrets.token_hex(24),'agent-token':secrets.token_hex(32),'admin.json':'{}'}.items():(host/'secrets'/name).write_text(value)
 f={**c,'root':str(root),'host':str(host),'data':str(root/'data')};env=load('write-environment');services=load('prepare-services');installer=load('install-host');checks=[]
 env.generate(f);versions=json.loads((host/'config/versions.json').read_text());assert len(versions)==9;checks.append('nine local/pinned images resolve; private environment generated')
 lockpath=root/'core/templates/versions.lock.json';original_lock=lockpath.read_bytes();invalid=json.loads(original_lock);invalid.pop('RESTIC_IMAGE');lockpath.write_text(json.dumps(invalid));original_env=(host/'config/compose.env').read_bytes()
 try:
  try:env.generate(f)
  except RuntimeError:checks.append('incomplete image lock fails closed without changing environment')
  else:raise RuntimeError('Incomplete lock manifest accepted')
 finally:lockpath.write_bytes(original_lock)
 assert (host/'config/compose.env').read_bytes()==original_env
 services.prepare(f,versions);checks.append('fresh app credentials/config, empty Git authorization and Syncthing setup generated')
 xml=host/'state/syncthing/config.xml';tree=ET.parse(xml);assert not tree.getroot().findall('folder');assert tree.findtext('gui/user')=='admin';assert tree.findtext('gui/password').startswith(('$2a$','$2b$','$2y$'));checks.append('Syncthing has bcrypt GUI authentication and zero folders before startup')
 identities={p.name:p.read_bytes() for p in (host/'state/syncthing').iterdir() if p.is_file()};files={p:p.read_bytes() for p in host.rglob('*') if p.is_file()};services.prepare(f,versions)
 assert all(p.read_bytes()==data for p,data in files.items());checks.append('repeated preparation preserves settings, credentials and device identity')
 original=xml.read_bytes();tree=ET.parse(xml);ET.SubElement(tree.getroot(),'folder',id='fixture',path='/never-started');tree.write(xml)
 try:
  try:services.prepare(f,versions)
  except RuntimeError:checks.append('configured sync folders rejected without identity regeneration')
  else:raise RuntimeError('Existing folders accepted unexpectedly')
 finally:xml.write_bytes(original)
 staged=installer.stage(c,work/'host-bundle');expected=installer.bundle(c)
 assert all((staged/str(path).lstrip('/')).read_bytes()==data for path,data in expected.items());checks.append('staged host tools/Compose/systemd definitions match source exactly')
 subprocess.run(DOCKER+['compose','--project-name','nas-preparation-validation','--env-file',str(host/'config/compose.env'),'-f',str(Path(c['root'])/'core/compose/nas.yml'),'--profile','*','config','--quiet'],check=True);checks.append('fresh generated settings validate through actual Docker Compose parser')
 name='nas-prepare-check-'+uuid.uuid4().hex[:10];created=False
 try:
  subprocess.run(DOCKER+['create','--name',name,'--label','nas.purpose=prepare-validation','--network','none','--read-only','--tmpfs','/tmp:size=16m','--tmpfs','/db:size=64m','-e','STHOMEDIR=','--mount',f'type=bind,source={host}/state/syncthing,target=/config','--entrypoint','/bin/syncthing',versions['SYNCTHING_IMAGE']['image'],'serve','--config=/config','--data=/db','--no-browser','--no-restart','--no-upgrade'],check=True,stdout=subprocess.DEVNULL);created=True
  subprocess.run(DOCKER+['start',name],check=True,stdout=subprocess.DEVNULL)
  for _ in range(60):
   r=subprocess.run(DOCKER+['exec',name,'sh','-c','wget -S -O /dev/null http://127.0.0.1:8384/rest/config/folders 2>&1'],capture_output=True,text=True)
   if '401' in r.stdout or '403' in r.stdout:break
   info=json.loads(subprocess.check_output(DOCKER+['inspect',name],text=True))[0]
   if info['State']['Status']!='running':raise RuntimeError('Isolated Syncthing exited')
   time.sleep(1)
  else:raise RuntimeError('Syncthing unauthenticated API did not reject unauthenticated access')
  checks.append('isolated Syncthing starts with no host ports; unauthenticated API rejects access')
  auth=base64.b64encode(('admin:'+password).encode()).decode()
  command='IFS= read -r auth; wget -qO- --header "Authorization: Basic $auth" http://127.0.0.1:8384/meta.js'
  r=subprocess.run(DOCKER+['exec','-i',name,'sh','-c',command],input=auth+'\n',capture_output=True,text=True,check=True)
  assert r.stdout.strip();checks.append('generated GUI credentials authenticate to protected metadata endpoint')
  apikey=ET.parse(xml).findtext('gui/apikey')
  command='IFS= read -r key; wget -qO- --header "X-API-Key: $key" http://127.0.0.1:8384/rest/config/folders'
  r=subprocess.run(DOCKER+['exec','-i',name,'sh','-c',command],input=apikey+'\n',capture_output=True,text=True,check=True)
  assert json.loads(r.stdout)==[];checks.append('authenticated API confirms running Syncthing has no sync folders')
 finally:
  if created:
   subprocess.run(DOCKER+['stop','--timeout','30',name],check=True,stdout=subprocess.DEVNULL)
   state=json.loads(subprocess.check_output(DOCKER+['inspect',name],text=True))[0]['State']
   if state['ExitCode']!=0 or state.get('OOMKilled'):raise RuntimeError('Unclean Syncthing fixture stop; container retained')
   subprocess.run(DOCKER+['rm',name],check=True,stdout=subprocess.DEVNULL)
 assert all((host/'state/syncthing'/name).read_bytes()==data for name,data in identities.items() if name in ('cert.pem','key.pem'));checks.append('device private key/certificate survive startup unchanged')
 assert fingerprint(c)==before;checks.append('production configuration/secrets unchanged')
 report={'state':'PASS','checks':checks,'scope':'Fresh service prep + actual isolated Syncthing; not a full second NAS install','work':str(work)}
 (work/'result.json').write_text(json.dumps(report,indent=2)+'\n');(Path(c['root'])/'system/runtime/acceptance/deployment-preparation.json').write_text(json.dumps(report,indent=2)+'\n');print('PASS:',len(checks),'deployment preparation checks. '+str(work/'result.json'))
if __name__=='__main__':main()
