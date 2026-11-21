#!/usr/bin/env python3
"""Exercise Immich ML through the authenticated application job/search API."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import datetime,json,time,urllib.request,urllib.error
from pathlib import Path
H=Path('/mnt/c/nas/system');ROOT=Path('/mnt/d/nas');op=urllib.request.build_opener(urllib.request.ProxyHandler({}));base='http://127.0.0.1:2283/api'
def request(path,data=None,token=None,method=None,timeout=300):
 headers={'Content-Type':'application/json'}
 if token:headers['Authorization']='Bearer '+token
 req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers,method=method)
 with op.open(req,timeout=timeout) as r:return json.load(r)
password=(H/'secrets/admin-password.txt').read_text().strip()
token=request('/auth/login',{'email':'admin@nas.local','password':password})['accessToken']
config=request('/system-config',token=token)
assert config['machineLearning']['enabled']
report={'state':'running','started':datetime.datetime.now(datetime.timezone.utc).isoformat(),'models':{'smart_search':config['machineLearning'].get('clip',{}),'facial_recognition':config['machineLearning'].get('facialRecognition',{})},'jobs':{}}
path=H/'state/check-20261116/machine-learning.json';path.write_text(json.dumps(report,indent=2))
try:
 for job in ['smartSearch','faceDetection']:
  request('/jobs/'+job,{'command':'start','force':True},token,'PUT')
  deadline=time.monotonic()+900
  while time.monotonic()<deadline:
   jobs=request('/jobs',token=token);current=jobs[job];counts=current.get('jobCounts',{})
   report['jobs'][job]=current;path.write_text(json.dumps(report,indent=2))
   if not current.get('queueStatus',{}).get('isActive') and not any(counts.get(k,0) for k in ('active','waiting','delayed','prioritized','waiting-children')):
    if counts.get('failed',0):raise RuntimeError(job+' has failed jobs')
    break
   time.sleep(5)
  else:raise RuntimeError(job+' timeout')
  print('PASS: '+job+' queue completed',flush=True)
 search=request('/search/smart',{'query':'a photo','size':10},token)
 report['search_items']=len(search.get('assets',{}).get('items',[]))
 expected=json.loads((ROOT/'system/runtime/acceptance/immich.json').read_text())['asset_id']
 assert expected in [item['id'] for item in search.get('assets',{}).get('items',[])], 'Uploaded fixture missing from semantic results'
 assert report['search_items']>=1, 'Visual index/search did not return the uploaded test asset'
 report['state']='PASS';report['checks']=['machine learning enabled in app','smart search image inference','face detection inference','text inference and semantic search returned indexed asset'];report['finished']=datetime.datetime.now(datetime.timezone.utc).isoformat()
 path.write_text(json.dumps(report,indent=2));print('PASS: machine-learning inference and semantic search.')
except Exception as e:
 report.update(state='failed',error=str(e));path.write_text(json.dumps(report,indent=2));raise
