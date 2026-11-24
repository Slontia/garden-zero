#!/usr/bin/env python3
"""Exercise readiness over actual isolated HTTP; no production services or accounts."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import contextlib,http.server,io,json,tempfile,threading,unittest
from nas_ready import wait_meta_ready
class Handler(http.server.BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def reply(self,code,body,cookie=None):
  data=body if isinstance(body,bytes) else json.dumps(body).encode();self.send_response(code);self.send_header('Content-Length',str(len(data)))
  if cookie:self.send_header('Set-Cookie',cookie)
  self.end_headers();self.wfile.write(data)
 def do_GET(self):
  s=self.server.options;self.server.calls.append(('GET',self.path))
  if self.path=='/health':return self.reply(200,{'mode':s.get('mode','meta'),'status':'ready'})
  if self.path=='/sso/login':
   self.send_response(s.get('sso_status',302));self.send_header('Location',s.get('sso_location','https://nas.test/auth/application/o/authorize/'));self.send_header('Content-Length','0');self.end_headers();return
  if 'probe=ok' not in self.headers.get('Cookie',''):return self.reply(401,{})
  if self.path=='/':return self.reply(200,b'<div id="modules"></div>' if s.get('dashboard',True) else b'<form id="login"></form>')
  self.server.status_calls+=1
  if self.server.status_calls<=s.get('delay',0):return self.reply(503,{'error':'controller starting'})
  modules={'meta':{'state':'ready'},'files':{'state':s.get('files','ready')}}
  self.reply(200,{'docker':s.get('docker','ready'),'data_access':{'state':s.get('mounts','verified')},'modules':modules,'storage_blocked':s.get('blocked',False)})
 def do_POST(self):
  self.server.calls.append(('POST',self.path));body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
  if self.path=='/api/logout':return self.reply(200,{'ok':True},'probe=; Max-Age=0; Path=/')
  ok=body=={'username':'admin','password':'fixture-secret'} and self.server.options.get('auth',True)
  self.reply(200 if ok else 401,{'ok':ok},'probe=ok; Path=/; HttpOnly' if ok else None)
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='nas-ready-test-');p=Path(self.tmp.name);(p/'secrets').mkdir();(p/'secrets/admin-password.txt').write_text('fixture-secret\n')
  self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.options={};self.server.calls=[];self.server.status_calls=0
  self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.config={'host':str(p),'ports':{'meta':self.server.server_port}}
 def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()
 def probe(self,**kw):
  with contextlib.redirect_stdout(io.StringIO()) as output:
   try:return wait_meta_ready(self.config,timeout=kw.pop('timeout',.25),interval=.01,**kw)
   finally:self.assertNotIn('fixture-secret',output.getvalue())
 def test_success_checks_authenticated_dashboard_status_and_logout(self):
  result=self.probe(modules=['meta','files']);self.assertEqual(result['state'],'ready');self.assertIn(('GET','/api/status'),self.server.calls);self.assertEqual(self.server.calls[-1],('POST','/api/logout'))
 def test_sso_discovery_redirect_is_required_when_enabled(self):
  self.config['sso']={'enabled':True,'origin':'https://nas.test'};self.assertEqual(self.probe()['state'],'ready');self.assertIn(('GET','/sso/login'),self.server.calls)
 def test_broken_sso_prevents_success(self):
  self.config['sso']={'enabled':True,'origin':'https://nas.test'};self.server.options['sso_status']=503
  with self.assertRaisesRegex(RuntimeError,'Unified login'):self.probe()
 def test_untrusted_sso_redirect_prevents_success(self):
  self.config['sso']={'enabled':True,'origin':'https://nas.test'};self.server.options['sso_location']='https://untrusted.test/auth/'
  with self.assertRaisesRegex(RuntimeError,'Unified login'):self.probe()
 def test_health_alone_cannot_report_ready(self):
  self.server.options['delay']=1000
  with self.assertRaisesRegex(RuntimeError,'timed out'):self.probe()
  self.assertGreater(self.server.status_calls,1)
 def test_controller_starting_is_retried(self):
  self.server.options['delay']=2;self.assertEqual(self.probe(timeout=2)['attempts'],3)
 def test_requested_business_service_must_be_ready(self):
  self.server.options['files']='starting'
  with self.assertRaisesRegex(RuntimeError,'files'):self.probe(modules=['files'])
 def test_unrequested_stopped_service_does_not_block(self):
  self.server.options['files']='stopped';self.assertEqual(self.probe()['state'],'ready')
 def test_bad_authentication_is_not_ready(self):
  self.server.options['auth']=False
  with self.assertRaisesRegex(RuntimeError,'HTTP 401'):self.probe()
 def test_login_redirect_is_not_dashboard(self):
  self.server.options['dashboard']=False
  with self.assertRaisesRegex(RuntimeError,'dashboard'):self.probe()
 def test_wrong_service_on_port_is_not_ready(self):
  self.server.options['mode']='files'
  with self.assertRaisesRegex(RuntimeError,'HTTP process'):self.probe()
  self.assertNotIn(('POST','/api/login'),self.server.calls)
 def test_docker_unavailable_is_not_ready(self):
  self.server.options['docker']='unavailable'
  with self.assertRaisesRegex(RuntimeError,'Docker'):self.probe()
 def test_mount_mismatch_is_not_ready(self):
  self.server.options['mounts']='unavailable'
  with self.assertRaisesRegex(RuntimeError,'permissions'):self.probe()
 def test_storage_block_prevents_success(self):
  self.server.options['blocked']=True
  with self.assertRaisesRegex(RuntimeError,'storage'):self.probe()
class StartupWiring(unittest.TestCase):
 def test_probe_precedes_success_audit(self):
  import nasctl,nas_access,nas_ready
  from unittest.mock import patch
  events=[]
  with tempfile.TemporaryDirectory() as work:
   c={'host':work,'project':'fixture','ml_enabled':False}
   with patch.object(nasctl,'remember'),patch.object(nasctl,'run'),patch.object(nasctl,'output',return_value='{}'),patch.object(nas_access,'validate_compose'),patch.object(nasctl,'compose',side_effect=lambda *a:events.append('compose')),patch.object(nasctl,'wait_services',side_effect=lambda *a:events.append('containers')),patch.object(nas_ready,'wait_meta_ready',side_effect=lambda *a:events.append('management')),patch.object(nasctl,'audit',side_effect=lambda *a:events.append('success')):
    nasctl.start(c,'meta')
  self.assertLess(events.index('containers'),events.index('management'));self.assertLess(events.index('management'),events.index('success'))
 def test_probe_failure_prevents_success_audit(self):
  import nasctl,nas_access,nas_ready
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as work:
   c={'host':work,'project':'fixture','ml_enabled':False}
   with patch.object(nasctl,'remember'),patch.object(nasctl,'run'),patch.object(nasctl,'output',return_value='{}'),patch.object(nas_access,'validate_compose'),patch.object(nasctl,'compose'),patch.object(nasctl,'wait_services'),patch.object(nas_ready,'wait_meta_ready',side_effect=RuntimeError('not ready')),patch.object(nasctl,'audit') as audit:
    with self.assertRaisesRegex(RuntimeError,'not ready'):nasctl.start(c,'meta')
    audit.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
