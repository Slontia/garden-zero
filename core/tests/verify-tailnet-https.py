"""End-to-end TLS checks via Tailscale's userspace dialer, without publishing extra ports."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import json,os,select,ssl,subprocess,time
from pathlib import Path
from urllib.parse import urlparse
HOST=urlparse(json.loads(Path('/mnt/c/nas/system/state/remote.json').read_text())['meta']).hostname
if not HOST:raise RuntimeError('Tailscale management origin is not configured')
def request(method,path,data=None,cookie=None):
 p=subprocess.Popen(['/usr/bin/docker','--host','unix:///var/run/docker.sock','exec','-i','nas-tailscale-1','tailscale','nc',HOST,'443'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 incoming=ssl.MemoryBIO();outgoing=ssl.MemoryBIO();tls=ssl.create_default_context().wrap_bio(incoming,outgoing,server_side=False,server_hostname=HOST);deadline=time.monotonic()+25
 def flush():
  while outgoing.pending:p.stdin.write(outgoing.read());p.stdin.flush()
 def receive():
  flush();remaining=deadline-time.monotonic()
  if remaining<=0 or not select.select([p.stdout],[],[],remaining)[0]:raise TimeoutError('Tailnet TLS timeout')
  block=os.read(p.stdout.fileno(),65536)
  if not block:incoming.write_eof();return False
  incoming.write(block);return True
 try:
  while True:
   try:tls.do_handshake();flush();break
   except ssl.SSLWantReadError:receive()
  body=json.dumps(data).encode() if data is not None else b''
  headers=[f'{method} {path} HTTP/1.1','Host: '+HOST,'Connection: close','Content-Type: application/json','X-NAS-Request: 1','Content-Length: '+str(len(body))]
  if cookie:headers.append('Cookie: '+cookie)
  tls.write(('\r\n'.join(headers)+'\r\n\r\n').encode()+body);flush();result=b''
  while True:
   try:
    block=tls.read(65536)
    if not block:break
    result+=block
    if b'\r\n\r\n' in result:
     head,payload=result.split(b'\r\n\r\n',1);items={k.strip().lower():v.strip() for line in head.split(b'\r\n')[1:] if b':' in line for k,v in [line.split(b':',1)]}
     if b'content-length' in items and len(payload)>=int(items[b'content-length']):break
   except ssl.SSLWantReadError:
    if not receive():break
   except (ssl.SSLEOFError,ssl.SSLZeroReturnError):break
  head,payload=result.split(b'\r\n\r\n',1);status=int(head.split(b' ',2)[1]);headers={k.decode().lower():v.decode().strip() for line in head.split(b'\r\n')[1:] if b':' in line for k,v in [line.split(b':',1)]}
  return status,headers,payload
 finally:p.kill();p.communicate()
results=[]
code,headers,body=request('GET','/');assert code==200 and b'id="login"' in body;results.append('TLS certificate/hostname verified; login page HTTP 200')
code,_,_=request('GET','/api/status');assert code==401;results.append('Unauthenticated tailnet API HTTP 401')
password=Path('/mnt/c/nas/system/secrets/admin-password.txt').read_text().strip()
code,headers,body=request('POST','/api/login',{'username':'admin','password':password});assert code==200
assert all(x in headers['set-cookie'] for x in ('Secure','HttpOnly','SameSite=Strict'));cookie=headers['set-cookie'].split(';',1)[0];results.append('HTTPS login and secure cookie flags verified')
code,_,body=request('GET','/api/status',cookie=cookie);assert code==200 and b'"docker": "ready"' in body;results.append('Authenticated status through HTTPS Serve and host agent HTTP 200')
code,_,_=request('POST','/api/logout',{},cookie);assert code==200;results.append('HTTPS logout succeeded')
Path('/mnt/d/nas/system/runtime/acceptance/tailnet-https.json').write_text(json.dumps({'state':'PASS','tests':results,'scope':'Server self-dial via actual Tailscale Serve; external phone confirmation remains required'},indent=2))
print('PASS:',len(results),'end-to-end Tailscale HTTPS/authentication checks. No credentials logged.')
