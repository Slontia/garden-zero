#!/usr/bin/env python3
"""Verify real Serve PROXY v2 + WhoIs on a temporary private port, then remove it."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import datetime,hashlib,ipaddress,json,os,select,socket,struct,subprocess,threading,time,uuid
from pathlib import Path
from nas_identity import SIGNATURE,address_header,node_identity
from nasctl import cfg,DOCKER
TS=DOCKER+['exec','nas-tailscale-1','tailscale']
def output(args):return subprocess.check_output(args,text=True).strip()
def main():
 c=cfg();checks=[]
 def frame(src='100.64.0.1',dst='100.64.0.2',family=0x11):
  data=ipaddress.ip_address(src).packed+ipaddress.ip_address(dst).packed+struct.pack('!HH',1234,24450)
  return SIGNATURE+bytes([0x21,family])+len(data).to_bytes(2,'big'),data
 h,p=frame();assert address_header(h,p)['source']=='100.64.0.1';checks.append('valid TCP4 PROXY v2')
 h6,p6=frame('fd7a:115c:a1e0::1','fd7a:115c:a1e0::2',0x21);assert address_header(h6,p6)['source'].endswith('::1');checks.append('valid TCP6 PROXY v2')
 bad=[(b'x'+h[1:],p),(h[:12]+b'\x20'+h[13:],p),(h[:13]+b'\x12'+h[14:],p),(h,p[:-1]),frame('127.0.0.1','100.64.0.2'),frame('100.64.0.1','8.8.8.8')]
 for header,payload in bad:
  try:address_header(header,payload)
  except ValueError:continue
  raise RuntimeError('Malformed PROXY header accepted')
 checks.append('malformed/local/UDP/non-tailnet headers rejected')
 for whois in ({'Node':{}},{'Node':{'StableID':'fixture','MachineAuthorized':True,'Addresses':['100.64.0.3/32']}}):
  try:node_identity('100.64.0.1',whois)
  except ValueError:continue
  raise RuntimeError('Missing or mismatched identity accepted')
 checks.append('WhoIs absence/address mismatch rejected')
 before=json.loads(output(TS+['serve','status','--json']));port=24450
 if str(port) in before.get('TCP',{}):raise RuntimeError('Identity test port already configured')
 status=json.loads(output(TS+['status','--json']));expected=status['Self']['ID'];ips=set(status['Self']['TailscaleIPs']);name=status['Self']['DNSName'].rstrip('.')
 listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(30);backend=listener.getsockname()[1];result={};configured=False
 def accept():
  try:
   conn,peer=listener.accept()
   with conn:
    conn.settimeout(20)
    if peer[0]!='127.0.0.1':raise RuntimeError('Untrusted proxy transport')
    def read_exact(size):
     data=b''
     while len(data)<size:
      block=conn.recv(size-len(data))
      if not block:raise RuntimeError('Truncated PROXY connection')
      data+=block
     return data
    header=read_exact(16);size=int.from_bytes(header[14:16],'big')
    if size>512:raise RuntimeError('Oversized proxy header')
    parsed=address_header(header,read_exact(size))
    if parsed['destination'] not in ips or parsed['destination_port']!=backend:raise RuntimeError('Incorrect destination identity')
    whois=json.loads(output(TS+['whois','--json',parsed['source']]))
    stable=node_identity(parsed['source'],whois)
    if stable!=expected:raise RuntimeError('Self-dial did not resolve to the actual node')
    if read_exact(4)!=b'NAS\n':raise RuntimeError('Unexpected probe body')
    result.update(destination_port_is_backend=True,identity_verified=True,node_id_sha256=hashlib.sha256(stable.encode()).hexdigest(),proxy_protocol=2)
    conn.sendall(b'IDENTITY_OK\n')
  except Exception as error:result['error']=str(error)
 thread=threading.Thread(target=accept,daemon=True);thread.start()
 try:
  subprocess.run(TS+['serve','--bg','--yes','--tcp='+str(port),'--proxy-protocol=2','tcp://127.0.0.1:'+str(backend)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE);configured=True
  probe=subprocess.Popen(DOCKER+['exec','-i','nas-tailscale-1','tailscale','nc',name,str(port)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  try:
   probe.stdin.write(b'NAS\n');probe.stdin.flush();reply=b'';deadline=time.monotonic()+35
   while not reply.endswith(b'\n'):
    remaining=deadline-time.monotonic()
    if remaining<=0 or not select.select([probe.stdout],[],[],remaining)[0]:raise TimeoutError('Identity probe reply timeout')
    block=os.read(probe.stdout.fileno(),1024)
    if not block:break
    reply+=block
  finally:probe.kill();probe.communicate()
  thread.join(timeout=2)
  if result.get('error'):raise RuntimeError(result['error'])
  if reply!=b'IDENTITY_OK\n' or not result.get('identity_verified'):raise RuntimeError('Real Serve identity probe failed')
  checks.append('real Tailscale Serve forwards original source via PROXY v2 and WhoIs resolves stable node ID')
 finally:
  if configured:subprocess.run(TS+['serve','--yes','--tcp='+str(port),'off'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
  listener.close()
 after=json.loads(output(TS+['serve','status','--json']))
 if before!=after:raise RuntimeError('Serve configuration changed; inspect without overwriting other routes')
 checks.append('temporary Serve route removed; all existing routes unchanged')
 report={'state':'PASS','checks':checks,'result':result,'limitations':['Server self-dial only; Mac/second-device identity and SMB session revocation still unverified','Not a deployed writer selector; all shares remain read-only','Trusted NAS host processes remain outside the remote-client trust boundary']}
 (Path(c['root'])/'system/runtime/acceptance/device-identity.json').write_text(json.dumps(report,indent=2)+'\n');print('PASS:',len(checks),'PROXY/WhoIs identity checks; production routes unchanged')
if __name__=='__main__':main()
