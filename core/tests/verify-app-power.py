#!/usr/bin/env python3
"""Exercise only knowledge service power; prove login and agent restart preserve state."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import http.cookiejar,json,subprocess,sys,time,urllib.request,urllib.error
from pathlib import Path
from nasctl import cfg,DOCKER,inventory
c=cfg();initial=inventory(c);before={r['service']:r for r in initial};active=before.get('joplin',{}).get('state')=='running';checks=[]
def session():return urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
op=session()
def request(path,data=None):
 req=urllib.request.Request('http://127.0.0.1:8080'+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json','X-NAS-Request':'1'})
 for attempt in range(40):
  try:
   with op.open(req,timeout=300) as r:return json.loads(r.read())
  except urllib.error.HTTPError as error:
   if path!='/api/status' or error.code not in (403,404,503) or attempt==39:raise
   time.sleep(.25)
def login():request('/api/login',{'username':'admin','password':(Path(c['host'])/'secrets/admin-password.txt').read_text().strip()})
def selection():return (Path(c['host'])/'state/desired-modules.json').read_bytes()
login();intent=selection()
try:
 request('/api/action',{'action':'app-power','app':'knowledge','enabled':False})
 s=request('/api/status');assert s['modules']['knowledge']['state']=='stopped' and 'knowledge' not in s['desired_modules'];checks.append('actual service stops and selection persists')
 stopped=selection();request('/api/logout',{});op=session();login();s=request('/api/status')
 assert s['modules']['knowledge']['state']=='stopped' and selection()==stopped;checks.append('logout and new login do not start stopped service')
 subprocess.run(['sudo','-n','systemctl','restart','nas-agent.service'],check=True)
 s=request('/api/status');assert s['modules']['knowledge']['state']=='stopped' and selection()==stopped;checks.append('controller restart preserves stopped choice')
 assert all(r['state']==before[r['service']]['state'] for r in inventory(c) if r['service']!='joplin');checks.append('all unrelated container states unchanged')
 if active:
  request('/api/action',{'action':'app-power','app':'knowledge','enabled':True});s=request('/api/status');assert s['modules']['knowledge']['state']=='ready';checks.append('actual service starts and becomes healthy')
  running=selection();request('/api/logout',{});op=session();login();assert request('/api/status')['modules']['knowledge']['state']=='ready' and selection()==running;checks.append('new login preserves running service')
 assert json.loads(selection())==json.loads(intent);checks.append('initial service selection restored')
 assert request('/api/status')['data_access']['state']=='verified';checks.append('live data mounts match registered owners')
finally:
 current={r['service']:r for r in inventory(c)}
 if active and current.get('joplin',{}).get('state')!='running':subprocess.run(['sudo','-n',sys.executable,str(Path(c['host'])/'installed/nasctl.py'),'start','knowledge'],check=True)
 elif not active and current.get('joplin',{}).get('state')=='running':request('/api/action',{'action':'app-power','app':'knowledge','enabled':False})
 request('/api/logout',{})
report={'state':'PASS','checks':checks,'scope':'Joplin container only; normal full host reboot not exercised'}
(Path(c['root'])/'system/runtime/acceptance/app-power.json').write_text(json.dumps(report,indent=2))
print('PASS:',len(checks),'live power/authentication/persistence checks; initial state restored')
