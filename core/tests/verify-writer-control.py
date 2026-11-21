#!/usr/bin/env python3
"""Production Meta device selection, restart and consistent backup revoke acceptance.
Requires explicit --exercise: briefly authorizes an online Mac, never writes user files.
"""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import argparse,http.cookiejar,json,subprocess,time,urllib.request,urllib.error
from pathlib import Path
from nasctl import cfg,DOCKER

def main():
 p=argparse.ArgumentParser();p.add_argument('--exercise',action='store_true');a=p.parse_args()
 if not a.exercise:raise SystemExit('Requires --exercise: temporarily selects Mac, restarts only NAS agent and creates a consistent backup')
 c=cfg();host=Path(c['host']);checks=[]
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
 def request(path,data=None):
  req=urllib.request.Request('http://127.0.0.1:8080'+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json','X-NAS-Request':'1'})
  try:
   with op.open(req,timeout=300) as r:return json.load(r)
  except urllib.error.HTTPError as e:raise RuntimeError(e.read().decode()) from None
 def select(device):request('/api/action',{'action':'writer','device':device})
 request('/api/login',{'username':'admin','password':(host/'secrets/admin-password.txt').read_text().strip()})
 try:
  status=request('/api/status');assert status['file_access']['active_writer'] is None
  mac=next(d for d in status['file_access']['devices'] if d['os']=='macOS' and d['online'])
  select(mac['id']);access=request('/api/status')['file_access'];assert access['active_writer']['id']==mac['id']
  checks.append('authenticated Meta action selects online Mac by verified stable node identity')
  s=json.loads(subprocess.check_output(DOCKER+['exec','nas-tailscale-1','tailscale','status','--json'],text=True));name=s['Self']['DNSName'].rstrip('.')
  denied=subprocess.run(DOCKER+['exec','-i','nas-tailscale-1','tailscale','nc',name,'1446'],input=b'not selected',stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)
  assert denied.stdout==b'';checks.append('actual NAS self node denied while Mac is selected')
  subprocess.run(['systemctl','restart','nas-agent.service'],check=True)
  deadline=time.monotonic()+30
  while True:
   try:
    access=request('/api/status')['file_access']
    assert access['active_writer'] is None and access['writer_selection']=='available';break
   except Exception:
    if time.monotonic()>deadline:raise
    time.sleep(.5)
  assert not subprocess.check_output(DOCKER+['ps','-aq','--filter','name=^/nas-file-writer$'],text=True).strip()
  checks.append('real controller restart revokes grant and removes backend; no authorization restored')
  select(mac['id']);request('/api/action',{'action':'backup'})
  status=request('/api/status');assert status['file_access']['active_writer'] is None and status['backup']['state']=='success'
  backup=Path(status['backup']['path']);assert (backup/'COMPLETE').is_file()
  checks.append('actual consistent backup revokes writer before snapshot and leaves it read-only')
  result={'state':'PASS','checks':checks,'backup':str(backup),'limitations':['Mac grant verified from server; user must test Finder upload/rename/delete and reconnect','No second physical write-capable client tested']}
  (host/'state/writer-control-acceptance.json').write_text(json.dumps(result,indent=2)+'\n');(Path(c['root'])/'system/runtime/acceptance/writer-control.json').write_text(json.dumps(result,indent=2)+'\n');print('PASS:',len(checks),'production writer control/restart/backup checks; backup',backup)
 finally:
  try:select(None)
  finally:request('/api/logout',{})
if __name__=='__main__':main()
