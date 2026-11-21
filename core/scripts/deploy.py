#!/usr/bin/env python3
"""Resume deployment after safe initialization, keeping remote authorization explicit."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import argparse,importlib.util,json,os,subprocess,sys,time,urllib.request,datetime
from pathlib import Path
from nasctl import cfg,volume,inventory,lock,DOCKER

def module(name):
 p=Path(__file__).with_name(name+'.py');spec=importlib.util.spec_from_file_location(name.replace('-','_'),p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def account(c):
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}));base='http://127.0.0.1:2283/api'
 def req(path,data=None):
  r=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
  with op.open(r,timeout=30) as response:return json.load(response)
 if req('/server/config')['isInitialized']:return 'existing_account_preserved'
 password=(Path(c['host'])/'secrets/admin-password.txt').read_text().strip()
 req('/auth/admin-sign-up',{'email':'admin@nas.local','name':'NAS Admin','password':password})
 return 'admin_initialized_with_local_credential'

def check_ports(c):
 # Enforce single-instance deployment and do not take over another application.
 import socket
 expected={'meta':8080,'files':8081,'knowledge':8082,'immich-server':2283,'git':2222,'syncthing':8384,'smb':1445}
 ids=subprocess.check_output(DOCKER+['ps','-q'],text=True).split()
 infos=json.loads(subprocess.check_output(DOCKER+['inspect']+ids,text=True)) if ids else []
 for service,port in expected.items():
  owned=False
  for row in infos:
   for binds in (row['NetworkSettings'].get('Ports') or {}).values():
    for bind in binds or []:
     if bind['HostPort']==str(port):
      labels=row['Config'].get('Labels') or {}
      if labels.get('com.docker.compose.project')!=c['project'] or labels.get('com.docker.compose.service')!=service or bind['HostIp']!='127.0.0.1':raise RuntimeError('Host port belongs to another service/interface: '+str(port))
      owned=True
  if not owned:
   with socket.socket() as s:
    try:s.bind(('127.0.0.1',port))
    except OSError:raise RuntimeError('Host port already occupied: '+str(port))

def main():
 p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');p.add_argument('--apply',action='store_true');p.add_argument('--build',action='store_true');p.add_argument('--fetch',action='store_true');p.add_argument('--allow-host-update',action='store_true');p.add_argument('--start',action='store_true');p.add_argument('--register-login',action='store_true');a=p.parse_args()
 if a.check and a.apply:raise RuntimeError('--check and --apply are mutually exclusive')
 if not a.apply and any([a.build,a.fetch,a.start,a.register_login,a.allow_host_update]):raise RuntimeError('Mutating options require --apply; default mode is read-only')
 if os.geteuid()!=0:raise RuntimeError('Use bash deploy.sh for sudo and Docker mount namespace')
 c=cfg();volume(c);check_ports(c)
 env=module('write-environment');services=module('prepare-services');installer=module('install-host')
 versions,missing=env.resolve(c);host_files=installer.review(installer.bundle(c));locks=json.loads((Path(c['root'])/'core/templates/versions.lock.json').read_text())
 if not a.apply:
  print(json.dumps({'mode':'read_only','missing_images':missing,'host_files':host_files,'external_authorization':'Existing identity preserved; a fresh Tailscale instance requires login','physical_disk_format':False,'start_requested':False},indent=2));return
 report={'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'state':'running','steps':[]};state=Path(c['host'])/'state/deployment.json'
 try:
  with lock():
   if a.build:
    for tag,directory in [('nas/web:1','web'),('nas/services:1','services')]:
     subprocess.run(DOCKER+['build','--pull=false','-t',tag,str(Path(c['root'])/'core'/directory)],check=True)
   for key in missing:
    if key in env.LOCAL:continue
    if not a.fetch:raise RuntimeError('Pinned image missing; --fetch required: '+key)
    subprocess.run(DOCKER+['pull',locks[key]['image']],check=True)
   report['steps'].append({'environment':env.generate(c)})
   versions=json.loads((Path(c['host'])/'config/versions.json').read_text())
   report['steps'].append({'services':services.prepare(c,versions)})
   subprocess.run(DOCKER+['compose','--project-name',c['project'],'--env-file',str(Path(c['host'])/'config/compose.env'),'-f',str(Path(c['root'])/'core/compose/nas.yml'),'--profile','*','config','--quiet'],check=True)
   cmd=[sys.executable,str(Path(__file__).with_name('install-host.py')),'--apply']
   if a.allow_host_update:cmd.append('--allow-update')
   subprocess.run(cmd,check=True);report['steps'].append({'host_files':'installed'})
  if any(row['action']=='update' and row['path'].endswith(tuple(installer.TOOLS)) for row in host_files):
   subprocess.run(['systemctl','restart','nas-agent.service'],check=True);report['steps'].append({'agent':'restarted_for_updated_tools'})
  if a.start:
   subprocess.run(['systemctl','enable','nas-agent.service','nas-session.service','nas-soak.timer'],check=True,stdout=subprocess.DEVNULL)
   subprocess.run(['systemctl','start','nas-agent.service','nas-soak.timer','nas-session.service'],check=True)
   report['steps'].append({'account':account(c) if any(r['service']=='immich-server' and r['state']=='running' for r in inventory(c)) else 'skipped_disabled_immich'})
  if a.register_login:
   script=Path(c['root'])/'core/scripts/register-startup.ps1';windows=subprocess.check_output(['wslpath','-w',str(script)],text=True).strip()
   subprocess.run(['/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe','-NoProfile','-NonInteractive','-File',windows,'-Distribution',os.environ.get('WSL_DISTRO_NAME','Ubuntu')],check=True)
   report['steps'].append({'windows_login':'registered_or_existing_verified'})
  report['state']='PASS';report['scope']='Current-host installation; fresh-host acceptance remains separate'
 except Exception as error:
  report['state']='FAIL';report['error']=str(error);raise
 finally:state.write_text(json.dumps(report,indent=2)+'\n')
 print('Deployment steps completed. Credentials and existing user content preserved. '+str(state))
if __name__=='__main__':
 try:main()
 except Exception as error:raise SystemExit('Deployment refused: '+str(error))
