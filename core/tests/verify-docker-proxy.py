#!/usr/bin/env python3
"""Controlled Docker restart acceptance; defaults to read-only diagnostics."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import argparse,datetime,hashlib,json,os,subprocess,sys,urllib.parse
from pathlib import Path
from nasctl import cfg,DOCKER,MODULES,wait_services,inventory,mounted,output

def run(args,**kwargs):return subprocess.run(args,check=True,**kwargs)
def info():return json.loads(output(DOCKER+['info','--format','{{json .}}']))
def outsiders():
 ids=output(DOCKER+['ps','-q']).split()
 if not ids:return []
 rows=json.loads(output(DOCKER+['inspect']+ids))
 return [r['Name'] for r in rows if (r['Config'].get('Labels') or {}).get('com.docker.compose.project')!='nas']

def main():
 p=argparse.ArgumentParser();p.add_argument('--restart',action='store_true');a=p.parse_args();c=cfg();initial=info()
 if not a.restart:
  print(json.dumps({'mode':'read_only','proxy_configured':bool(initial.get('HttpsProxy')),'non_nas_running':len(outsiders())}));return
 if os.geteuid()!=0:raise RuntimeError('Requires root')
 if outsiders():raise RuntimeError('Other Docker workloads are running; their owner must coordinate a restart')
 if subprocess.run(['systemctl','is-active','--quiet','nas-session.service']).returncode:raise RuntimeError('NAS systemd lifecycle is not active; inspect before restart')
 state=Path(c['host'])/'state';last=json.loads((state/'backup.json').read_text());backup=Path(last['path'])
 if last['state']!='success' or not (backup/'COMPLETE').is_file():raise RuntimeError('A successful COMPLETE backup is required')
 manifest=json.loads((backup/'manifest.json').read_text())
 for name,expected in manifest['files'].items():
  if Path(name).name!=name:raise RuntimeError('Unsafe backup manifest')
  h=hashlib.sha256()
  with (backup/name).open('rb') as f:
   for block in iter(lambda:f.read(1024**2),b''):h.update(block)
  if h.hexdigest()!=expected:raise RuntimeError('Backup checksum mismatch')
 disabled=state/'disabled.json';old_disabled=disabled.read_bytes() if disabled.exists() else None
 dropin=Path('/etc/systemd/system/docker.service.d/20-wsl-proxy.conf');old_dropin=dropin.read_bytes() if dropin.exists() else None
 record={'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'state':'running','backup':backup.name,'checks':['pre-restart COMPLETE backup checksums']};evidence=state/'docker-proxy-acceptance.json';stopped=False
 def restore_selection():
  if old_disabled is None:
   if disabled.exists():disabled.unlink()
  else:disabled.write_bytes(old_disabled)
 try:
  run([sys.executable,str(Path(__file__).resolve().parents[1]/'scripts/docker-wsl-proxy.py'),'--install'])
  run(['systemctl','stop','nas-session.service']);stopped=True
  if any(r['state']=='running' for r in inventory(c)) or mounted(c) or output(['losetup','-j',c['image']]):raise RuntimeError('NAS did not cleanly stop and detach before Docker restart')
  if outsiders():raise RuntimeError('Another workload started; Docker restart cancelled')
  record['checks'].append('all NAS services stopped; database mount and loop detached')
  run(['systemctl','restart','docker.service'])
  current=info()
  if current['DockerRootDir']!=initial['DockerRootDir']:raise RuntimeError('Shared Docker data root unexpectedly changed')
  if not current.get('HttpProxy') or not current.get('HttpsProxy'):raise RuntimeError('Docker did not load generated proxy environment')
  proxy=urllib.parse.urlsplit(current['HttpsProxy'])
  record['proxy']={'host':proxy.hostname,'port':proxy.port};record['checks'].append('persistent unit generates runtime proxy before Docker startup; Docker storage root unchanged')
  restore_selection();run(['systemctl','restart','nas-agent.service']);run(['systemctl','start','nas-session.service']);stopped=False
  wait_services(c,sum(MODULES.values(),[]),timeout=240);record['checks'].append('all eleven NAS services healthy after controlled Docker restart')
  lock=json.loads((Path(c['root'])/'core/templates/versions.lock.json').read_text())
  run(DOCKER+['pull',lock['RESTIC_IMAGE']['image']]);record['checks'].append('actual pinned registry pull succeeds through daemon proxy')
  record['state']='PASS'
 except Exception as error:
  record['state']='FAIL';record['error']=str(error)
  if subprocess.run(['systemctl','is-active','--quiet','docker.service']).returncode:
   if old_dropin is None:
    if dropin.exists():dropin.unlink()
   else:dropin.write_bytes(old_dropin)
   run(['systemctl','daemon-reload']);run(['systemctl','start','docker.service'])
  if stopped:
   restore_selection();run(['systemctl','start','nas-session.service']);wait_services(c,sum(MODULES.values(),[]),timeout=240)
  raise
 finally:evidence.write_text(json.dumps(record,indent=2)+'\n')
 (Path(c['root'])/'system/runtime/acceptance/docker-proxy.json').write_text(json.dumps(record,indent=2)+'\n')
 print('PASS: persistent Docker proxy, controlled clean restart and real registry pull. '+str(evidence))
if __name__=='__main__':main()
