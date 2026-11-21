#!/usr/bin/env python3
"""Exercise device isolation and revocation on real socket streams (no production writes)."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import ipaddress,json,socket,struct,threading,time,tempfile,unittest.mock as mock
from pathlib import Path
import nas_writer as w
from nas_identity import SIGNATURE,node_identity

def main():
 checks=[];events=[];writer=w.Writer({},lambda *args:events.append(args));writer.ready=True
 own='100.64.0.1';other='100.64.0.2';dst='100.64.0.3'
 def identity(args,**kw):
  source=args[-1]
  return type('Result',(),{'stdout':json.dumps({'Node':{'StableID':source,'MachineAuthorized':True,'Addresses':[source+'/32']}})})()
 backend=socket.socket();backend.bind(('127.0.0.1',0));backend.listen(8);backend.settimeout(.4)
 def start(source,epoch=None,raw=None):
  client,server=socket.socketpair();writer.slots.acquire();epoch=writer.epoch if epoch is None else epoch
  thread=threading.Thread(target=writer.handle,args=(server,('127.0.0.1',1234),epoch),daemon=True);thread.start()
  body=ipaddress.ip_address(source).packed+ipaddress.ip_address(dst).packed+struct.pack('!HH',1234,w.GATEWAY)
  client.sendall(raw if raw is not None else SIGNATURE+b'\x21\x11'+len(body).to_bytes(2,'big')+body)
  client.settimeout(2);return client,thread
 with mock.patch.object(w,'ts_status',return_value={'BackendState':'Running','Self':{'ID':own,'TailscaleIPs':[dst]},'Peer':{'b':{'ID':other,'InNetworkMap':True,'Online':True,'TailscaleIPs':[other]}}}),mock.patch.object(w,'command',side_effect=identity),mock.patch.object(w,'BACKEND',backend.getsockname()[1]),mock.patch.object(writer,'inspect',return_value=None):
  writer.active={'id':own,'name':'A'}
  c,t=start(other);t.join(2);assert c.recv(1)==b'';c.close()
  try:backend.accept();raise AssertionError('Other device reached backend')
  except socket.timeout:pass
  checks.append('other stable device denied despite same SMB credentials')
  c,t=start(own);b,_=backend.accept();b.settimeout(2);c.sendall(b'A-write');assert b.recv(20)==b'A-write';b.sendall(b'reply');assert c.recv(20)==b'reply'
  checks.append('selected identity relays bidirectional bytes')
  old_epoch=writer.epoch;writer.revoke('test-switch');t.join(2);assert not t.is_alive() and c.recv(1)==b'' and b.recv(1)==b'';assert not writer.sessions
  checks.append('revocation closes both ends of an established connection')
  writer.active={'id':other,'name':'B'}
  c2,t2=start(own);t2.join(2);assert c2.recv(1)==b'';c2.close()
  c2,t2=start(other,old_epoch);t2.join(2);assert c2.recv(1)==b'';c2.close()
  checks.append('old identity and a pre-switch handshake cannot reconnect')
  c2,t2=start(other);b2,_=backend.accept();c2.sendall(b'B-write');assert b2.recv(20)==b'B-write';writer.revoke('test-end');t2.join(2);c2.close();b2.close();c.close();b.close()
  checks.append('new identity only accepted after revocation')
  writer.active={'id':own};c,t=start(own,raw=b'X'*16);t.join(2);assert c.recv(1)==b'';c.close();writer.revoke('malformed-end')
  checks.append('non-PROXY traffic rejected')
  assert w.Writer({},lambda *a:None).active is None;checks.append('new controller instance has no persisted write authorization')
 backend.close()
 peer={'ID':'remote','InNetworkMap':True,'Online':True,'TailscaleIPs':[own]};whois={'Node':{'StableID':'remote','Addresses':[own+'/32'],'KeyExpiry':'2099-01-01T00:00:00Z'}}
 assert node_identity(own,whois,peer)=='remote'
 for patch_value in ({'Online':False},{'InNetworkMap':False},{'ID':'wrong'},{'Expired':True}):
  try:node_identity(own,whois,{**peer,**patch_value});raise AssertionError('Invalid peer accepted')
  except ValueError:pass
 whois['Node']['KeyExpiry']='2000-01-01T00:00:00Z'
 try:node_identity(own,whois,peer);raise AssertionError('Expired key accepted')
 except ValueError:pass
 checks.append('remote netmap identity, offline/revoked/mismatched nodes and expired keys validated')
 # Filesystem boundaries use isolated new fixtures only.
 with tempfile.TemporaryDirectory() as tmp:
  base=Path(tmp);(base/'data/shared').mkdir(parents=True);(base/'host/state/syncthing').mkdir(parents=True);xml=base/'host/state/syncthing/config.xml';xml.write_text('<configuration/>');cfg={'data':str(base/'data'),'host':str(base/'host')};w.safe_root(cfg)
  (base/'data/shared/link').symlink_to(base/'host')
  try:w.safe_root(cfg);raise AssertionError('Symlink accepted')
  except RuntimeError:pass
  (base/'data/shared/link').unlink();xml.write_text('<configuration><folder path="/other"/></configuration>')
  try:w.safe_root(cfg);raise AssertionError('Sync bypass accepted')
  except RuntimeError:pass
  checks.append('symlink paths and uncoordinated synchronization fail closed')
 result={'state':'PASS','checks':checks,'limitations':['Simulated WhoIs identities on real socket streams; production Tailscale and actual SMB covered separately','Actual Mac and two-device simultaneous mounting still require clients']}
 out=Path(__file__).resolve().parents[2]/'system/runtime/acceptance/writer-isolation.json';out.write_text(json.dumps(result,indent=2)+'\n');print('PASS:',len(checks),'writer isolation/revocation checks')
if __name__=='__main__':main()
