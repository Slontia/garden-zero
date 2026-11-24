#!/usr/bin/env python3
"""Configure only NAS Serve routes, preserve unrelated configuration and acceptance records."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import importlib.util,json,subprocess
from pathlib import Path
from nasctl import cfg,DOCKER,lock,compose
TS=DOCKER+['exec','nas-tailscale-1','tailscale']
def main():
 c=cfg();status=json.loads(subprocess.check_output(TS+['status','--json'],text=True))
 if status.get('BackendState')!='Running':raise RuntimeError('Tailscale login required; no services changed')
 name=status.get('Self',{}).get('DNSName','').rstrip('.')
 if not name:raise RuntimeError('Tailscale DNS identity missing')
 current=json.loads(subprocess.check_output(TS+['serve','status','--json'],text=True))
 routes=[('https',443,'http://127.0.0.1:8080'),('https',8444,'http://127.0.0.1:8082'),('https',8445,'http://127.0.0.1:2283'),('http',80,'http://127.0.0.1:8080'),('tcp',2222,'tcp://127.0.0.1:2222'),('tcp',445,'tcp://127.0.0.1:1445')]
 if c.get('file_access',{}).get('mode')=='full_sync':routes.append(('tcp',22000,'tcp://127.0.0.1:22000'))
 else:routes.append(('tcp-writer',1446,'tcp://127.0.0.1:1446'))
 if any(current.get('AllowFunnel',{}).get(name+':'+str(port)) for _,port,_ in routes):raise RuntimeError('A NAS port is configured for public Funnel; inspect before private setup')
 for protocol,port,target in routes:
  entry=current.get('TCP',{}).get(str(port))
  if not entry:continue
  if protocol in ('tcp','tcp-writer'):
   expected={'TCPForward':target.removeprefix('tcp://')}
   if protocol=='tcp-writer':expected['ProxyProtocol']=2
   if entry!=expected:raise RuntimeError('Existing TCP route differs; inspect before replacement: '+str(port))
  else:
   if entry!={('HTTPS' if protocol=='https' else 'HTTP'):True}:raise RuntimeError('Existing HTTP listener differs: '+str(port))
   web=current.get('Web',{}).get(name+':'+str(port),{})
   handlers=web.get('Handlers',{})
   expected_handlers={'/':{'Proxy':target}}
   if port==443 and c.get('sso',{}).get('clients_prepared'):expected_handlers['/auth/']={'Proxy':'http://127.0.0.1:9000/auth/'}
   if handlers!=expected_handlers:raise RuntimeError('Existing web routes differ; inspect before replacement: '+str(port))
 host=Path(c['host']);path=host/'state/remote.json';prior=json.loads(path.read_text()) if path.exists() else {}
 with lock():
  for protocol,port,target in routes:
   flags=['--tcp='+str(port),'--proxy-protocol=2'] if protocol=='tcp-writer' else ['--'+protocol+'='+str(port)]
   subprocess.run(TS+['serve','--bg','--yes',*flags,target],check=True,timeout=30,stdout=subprocess.DEVNULL)
  result={**prior,'state':'configured_not_client_verified','name':name,'meta':'https://'+name,'files':'https://'+name+':8443','knowledge':'https://'+name+':8444','immich':'https://'+name+':8445','git':'ssh://nas@'+name+':2222/','smb':chr(92)*2+name+chr(92)+'nas','note':'Private Serve only. External-client/policy acceptance tracked separately.'}
  path.write_text(json.dumps(result,indent=2)+'\n')
  script=Path(__file__).with_name('write-environment.py');spec=importlib.util.spec_from_file_location('nas_environment',script);env=importlib.util.module_from_spec(spec);spec.loader.exec_module(env);env.generate(c)
  # A newly authorized hostname must reach the running Meta environment as well.
  compose(c,'up','-d','--no-build','--wait','--wait-timeout','90','meta')
 print('Private NAS routes configured; management confirmation records preserved. '+result['meta'])
if __name__=='__main__':
 try:main()
 except Exception as error:raise SystemExit('Remote configuration refused: '+str(error))
