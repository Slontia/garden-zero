#!/usr/bin/env python3
"""Bounded API acceptance phases; disable/restore only optional intelligence."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import argparse,http.cookiejar,json,urllib.request,urllib.error,time
from pathlib import Path
host=Path('/mnt/c/nas/system');root=Path(__file__).resolve().parents[2];statefile=host/'state/app-capability-acceptance.json'
a=argparse.ArgumentParser();a.add_argument('phase',choices=['read','disable','restore']);phase=a.parse_args().phase
op=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()));checks=[]
def req(path,data=None):
 r=urllib.request.Request('http://127.0.0.1:8080'+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json','X-NAS-Request':'1'})
 try:
  with op.open(r,timeout=50) as response:return response.status,response.read()
 except urllib.error.HTTPError as error:return error.code,error.read()
def get(path):
 code,data=req(path);assert code==200,(path,code);return json.loads(data)
def action(data):
 code,body=req('/api/action',data);assert code==200,(code,body.decode()[:100])
def check(name,condition):
 if not condition:raise AssertionError(name)
 checks.append(name);print('PASS: '+name,flush=True)
if phase=='read':
 for path in ['/api/system','/api/app-logs/immich','/api/container-logs/database']:check('authentication required '+path,req(path)[0]==401)
code,_=req('/api/login',{'username':'admin','password':(host/'secrets/admin-password.txt').read_text().strip()});assert code==200
record=json.loads(statefile.read_text()) if statefile.exists() else {}
try:
 if phase=='read':
  system=get('/api/system');check('eleven NAS containers with actual ownership',len(system['containers'])==11 and sum(r['app']=='immich' for r in system['containers'])==3)
  check('running containers have real CPU memory uptime',all(r['cpu_percent'] is not None and r['memory_bytes'] is not None and r['uptime_seconds']>=0 for r in system['containers'] if r['state']=='running'))
  check('actual mount access without environment or secret contents',any(m['read_only'] for r in system['containers'] for m in r['mounts']) and all('Env' not in r and 'Config' not in r for r in system['containers']))
  check('app and container log scopes work',bool(get('/api/app-logs/immich')['logs']) and bool(get('/api/container-logs/database')['logs']))
  check('unrelated Docker log service rejected',req('/api/container-logs/outside-project')[0]!=200)
  check('platform application protected',req('/api/action',{'action':'app-power','app':'platform','enabled':False})[0]==400)
  check('arbitrary feature names rejected',req('/api/action',{'action':'app-feature','app':'immich','feature':'database','enabled':False})[0]==400)
 elif phase=='disable':
  before=get('/api/status');assert before['apps']['immich']['state']=='ready'
  record['original_selection']=json.loads((host/'state/desired-modules.json').read_text());record['feature_enabled']=before['apps']['immich']['features'][0]['enabled'];record['parent_started']={r['service']:r['started_at'] for r in get('/api/system')['containers'] if r['service'] in ('immich-server','database','cache')};record['pending_restore']=True;statefile.write_text(json.dumps(record,indent=2))
  action({'action':'app-feature','app':'immich','feature':'intelligence','enabled':False});off=get('/api/status');check('intelligence disables while album remains ready',off['apps']['immich']['state']=='ready' and not off['apps']['immich']['features'][0]['enabled'] and off['modules']['ml']['state']=='stopped')
  for attempt in range(20):
   ml=next(r for r in get('/api/system')['containers'] if r['service']=='immich-machine-learning')
   if ml['state']=='exited':break
   time.sleep(.4)
  check('stopped container shows unknown resource metrics',ml['state']=='exited' and ml['cpu_percent'] is None and ml['memory_bytes'] is None and ml['uptime_seconds'] is None)
 else:
  assert record.get('pending_restore'), 'No feature acceptance awaiting restoration'
  action({'action':'app-feature','app':'immich','feature':'intelligence','enabled':record['feature_enabled']});after=get('/api/status');check('feature preference restored with album healthy',after['apps']['immich']['features'][0]['enabled']==record['feature_enabled'] and after['apps']['immich']['state']=='ready')
  system=get('/api/system');check('album database and cache did not restart',{r['service']:r['started_at'] for r in system['containers'] if r['service'] in ('immich-server','database','cache')}==record['parent_started'])
  check('all original application choices restored',json.loads((host/'state/desired-modules.json').read_text())==record['original_selection']);record['pending_restore']=False
 record[phase]={'state':'PASS','checks':checks};statefile.write_text(json.dumps(record,indent=2))
 if phase=='restore':(root/'system/runtime/acceptance/app-capabilities.json').write_text(json.dumps(record,indent=2))
finally:req('/api/logout',{})
print('Completed acceptance phase: '+phase,flush=True)
