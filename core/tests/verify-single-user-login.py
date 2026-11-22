#!/usr/bin/env python3
"""Real web handler with fixture credentials; no production credentials or control socket."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import hashlib,http.cookiejar,http.server,json,threading,unittest,urllib.request,urllib.error
from html.parser import HTMLParser
from unittest.mock import patch

class Inputs(HTMLParser):
 def __init__(self):super().__init__();self.inputs=[]
 def handle_starttag(self,tag,attrs):
  if tag=='input':self.inputs.append(dict(attrs))

class Tests(unittest.TestCase):
 def setUp(self):
  root=Path(__file__).resolve().parents[1]/'web';original=Path.read_text
  admin={'username':'solo','salt':'00112233445566778899aabbccddeeff','hash':hashlib.scrypt(b'fixture-password',salt=bytes.fromhex('00112233445566778899aabbccddeeff'),n=16384,r=8,p=1).hex()}
  def read(path,*args,**kwargs):
   if str(path)=='/credentials/admin.json':return json.dumps(admin)
   if str(path).startswith('/app/'):return original(root/path.relative_to('/app'),*args,**kwargs)
   return original(path,*args,**kwargs)
  source=original(root/'app.py');self.mock=patch.object(Path,'read_text',read);self.mock.start();self.addCleanup(self.mock.stop)
  self.ns={'__name__':'single_user_fixture'};exec(compile(source,str(root/'app.py'),'exec'),self.ns);self.ns['MODE']='meta';self.ns['PUBLIC_ORIGIN']=''
  self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),self.ns['Handler']);thread=threading.Thread(target=self.server.serve_forever,daemon=True);thread.start();self.addCleanup(self.close)
  self.op=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
 def close(self):self.server.shutdown();self.server.server_close()
 def req(self,path='/',body=None,headers=None):
  h={'Content-Type':'application/json','X-NAS-Request':'1'} if headers is None else headers
  request=urllib.request.Request('http://127.0.0.1:'+str(self.server.server_port)+path,data=json.dumps(body).encode() if body is not None else None,headers=h)
  try:response=self.op.open(request,timeout=5)
  except urllib.error.HTTPError as response:
   with response:return response.code,response.read().decode(),response.headers
  with response:return response.status,response.read().decode(),response.headers
 def test_password_only_login_uses_configured_identity(self):
  self.assertEqual(self.req('/api/login',{'password':'fixture-password'})[0],200);self.assertIn('id="modules"',self.req()[1])
 def test_explicit_username_clients_remain_supported(self):self.assertEqual(self.req('/api/login',{'username':'solo','password':'fixture-password'})[0],200)
 def test_wrong_explicit_identity_is_rejected(self):self.assertEqual(self.req('/api/login',{'username':'other','password':'fixture-password'})[0],401)
 def test_wrong_password_is_rejected(self):self.assertEqual(self.req('/api/login',{'password':'wrong'})[0],401)
 def test_request_header_protection_remains(self):self.assertEqual(self.req('/api/login',{'password':'fixture-password'},headers={'Content-Type':'application/json'})[0],403)
 def test_rate_limit_still_rejects_correct_password_after_failures(self):
  for _ in range(10):self.assertEqual(self.req('/api/login',{'password':'wrong'})[0],401)
  self.assertEqual(self.req('/api/login',{'password':'fixture-password'})[0],429)
 def test_logout_revokes_existing_cookie(self):
  self.req('/api/login',{'password':'fixture-password'});self.assertEqual(self.req('/api/logout',{})[0],200);self.assertIn('id="login"',self.req()[1])
 def test_owned_login_pages_only_display_password(self):
  for mode in ['meta','files']:
   self.ns['MODE']=mode;page=self.req()[1];parser=Inputs();parser.feed(page)
   visible=[field for field in parser.inputs if field.get('type')!='hidden'];self.assertEqual([field.get('name') for field in visible],['password']);self.assertTrue(any(field.get('name')=='username' and field.get('value')=='solo' for field in parser.inputs))
if __name__=='__main__':unittest.main(verbosity=2)
