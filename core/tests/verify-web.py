import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import http.cookiejar,json,urllib.request,urllib.error,os
from pathlib import Path
H=Path('/mnt/c/nas/system');root=Path('/mnt/d/nas');password=(H/'secrets/admin-password.txt').read_text().strip()
results=[]
def request(port,path,data=None,opener=None,method=None,header=True):
 req=urllib.request.Request('http://127.0.0.1:'+str(port)+path,data=json.dumps(data).encode() if data is not None else None,method=method,headers={'Content-Type':'application/json',**({'X-NAS-Request':'1'} if header else {})})
 try:
  with (opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))).open(req,timeout=300) as r:return r.status,r.read()
 except urllib.error.HTTPError as e:return e.code,e.read()
def check(name,value):
 if not value:raise AssertionError(name)
 results.append({'test':name,'result':'PASS'})
for port in (8080,8081,8082):
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
 check(f'{port} unauth API denied',request(port,'/api/status')[0]==401)
 check(f'{port} wrong password denied',request(port,'/api/login',{'username':'admin','password':'invalid'},opener)[0]==401)
 check(f'{port} login',request(port,'/api/login',{'username':'admin','password':password},opener)[0]==200)
 check(f'{port} authenticated page',request(port,'/',opener=opener)[0]==200)
 if port==8080:
  status,body=request(port,'/api/status',opener=opener);check('Meta control socket status',status==200 and json.loads(body)['docker']=='ready')
  check('arbitrary command rejected',request(port,'/api/action',{'action':'exec','command':'id'},opener)[0]==400)
  check('unknown module rejected',request(port,'/api/action',{'action':'start','module':'../../host'},opener)[0]==400)
  check('CSRF missing custom header rejected',request(port,'/api/action',{'action':'start','module':'files'},opener,header=False)[0]==403)
 if port==8081:
  sample=root/'data/shared/acceptance.txt';sample.write_text('NAS read-only acceptance sample\n')
  check('file download',request(port,'/browse?path=shared%2Facceptance.txt',opener=opener)[1]==sample.read_bytes())
  check('traversal denied',request(port,'/browse?path=..%2Fsystem',opener=opener)[0]==403)
  link=root/'data/shared/escape-test'
  link.symlink_to('/etc/passwd')
  try:check('symlink escape denied',request(port,'/browse?path=shared%2Fescape-test',opener=opener)[0]==403)
  finally:link.unlink()
  before=sample.read_bytes()
  for method in ('PUT','DELETE','PATCH'):check('read-only '+method,request(port,'/browse?path=shared%2Facceptance.txt',data={},opener=opener,method=method)[0]==405)
  check('read-only POST',request(port,'/upload',{},opener)[0]==405)
  check('content unchanged',sample.read_bytes()==before)
 if port==8082:
  check('knowledge search',b'welcome.md' in request(port,'/?q=articles',opener=opener)[1])
  check('knowledge article',request(port,'/?article=welcome.md',opener=opener)[0]==200)
 check(f'{port} logout',request(port,'/api/logout',{},opener)[0]==200)
 check(f'{port} session revoked',request(port,'/api/status',opener=opener)[0]==401)
(root/'system/runtime/acceptance/web.json').write_text(json.dumps(results,indent=2))
print('PASS:',len(results),'web auth/control/read-only checks; no secrets logged.')
