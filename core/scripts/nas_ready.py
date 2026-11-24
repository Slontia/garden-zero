"""Bounded, authenticated management readiness checks; no application state changes."""
import sys
sys.dont_write_bytecode=True
import http.cookiejar,json,time,urllib.error,urllib.request,urllib.parse
from pathlib import Path

def wait_meta_ready(config,modules=('meta',),timeout=120,interval=2):
 if timeout<=0 or interval<=0:raise ValueError('Positive readiness timeout and interval required')
 port=int(config['ports']['meta'])
 if not 0<port<65536:raise ValueError('Invalid management port')
 password=(Path(config['host'])/'secrets/admin-password.txt').read_text().strip()
 required=set(modules)|{'meta'};deadline=time.monotonic()+timeout;attempts=0;reason='No successful response'
 print('Waiting for management readiness: login, dashboard and host status...',flush=True)
 while time.monotonic()<deadline:
  attempts+=1;cookies=http.cookiejar.CookieJar();opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(cookies));logged_in=False
  def request(path,data=None,cleanup=False):
   remaining=deadline-time.monotonic()
   if remaining<=0 and not cleanup:raise TimeoutError('readiness deadline')
   limit=min(5,max(0.05,remaining)) if not cleanup else min(0.5,max(0.05,remaining))
   payload=json.dumps(data).encode() if data is not None else None
   req=urllib.request.Request('http://127.0.0.1:'+str(port)+path,data=payload,headers={'Content-Type':'application/json','X-NAS-Request':'1'})
   with opener.open(req,timeout=limit) as response:return response.read()
  try:
   health=json.loads(request('/health'))
   if health.get('mode')!='meta' or health.get('status')!='ready':raise RuntimeError('Management HTTP process is not ready')
   login=json.loads(request('/api/login',{'username':'admin','password':password}))
   if login.get('ok') is not True:raise RuntimeError('Management authentication is not ready')
   logged_in=True
   page=request('/')
   if b'id="modules"' not in page or b'id="login"' in page:raise RuntimeError('Authenticated dashboard did not render')
   status=json.loads(request('/api/status'))
   if status.get('docker')!='ready':raise RuntimeError('Host controller cannot query Docker')
   if status.get('data_access',{}).get('state')!='verified':raise RuntimeError('Data mount permissions are not verified')
   if status.get('storage_blocked'):raise RuntimeError('NAS storage is blocked')
   pending=sorted(name for name in required if status.get('modules',{}).get(name,{}).get('state')!='ready')
   if pending:raise RuntimeError('Modules not ready: '+', '.join(pending))
   if config.get('sso',{}).get('enabled'):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
     def redirect_request(self,*args,**kwargs):return None
    probe=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    try:
     with probe.open('http://127.0.0.1:'+str(port)+'/sso/login',timeout=min(5,max(0.05,deadline-time.monotonic()))) as response:raise RuntimeError('Unified login did not redirect to the identity provider')
    except urllib.error.HTTPError as response:
     try:
      location=urllib.parse.urlsplit(response.headers.get('Location',''));expected=urllib.parse.urlsplit(config['sso']['origin'])
      if response.code!=302 or location.scheme!='https' or location.netloc!=expected.netloc or not location.path.startswith('/auth/'):raise RuntimeError('Unified login discovery is not ready')
     finally:response.close()
   print('Management ready: authenticated dashboard, host control and requested modules verified.',flush=True)
   return {'state':'ready','attempts':attempts,'modules':sorted(required)}
  except urllib.error.HTTPError as error:
   reason='Management HTTP '+str(error.code);error.close()
   if error.code in (401,403) and error.url.endswith('/api/login'):raise RuntimeError('Management authentication failed (HTTP '+str(error.code)+')') from None
  except RuntimeError as error:reason=str(error)
  except (OSError,ValueError) as error:
   # Preserve the meaningful service error if the final attempt exhausts the deadline.
   if reason=='No successful response':reason=type(error).__name__
  finally:
   if logged_in:
    try:request('/api/logout',{},cleanup=True)
    except Exception:pass
  remaining=deadline-time.monotonic()
  if remaining>0:time.sleep(min(interval,remaining))
 raise RuntimeError('Management readiness timed out after '+str(timeout)+'s: '+reason)
