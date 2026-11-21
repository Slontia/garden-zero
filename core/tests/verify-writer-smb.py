#!/usr/bin/env python3
"""Real private Serve -> identity gateway -> ephemeral SMB, then revoke and cleanup.

Temporarily selects the NAS self identity in an isolated controller; never grants a user's device.
"""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import contextlib,datetime,json,os,select,socket,subprocess,tempfile,threading,time,uuid
from pathlib import Path
from unittest.mock import patch
import nas_writer as w
from nasctl import cfg,audit,lock

def main():
 c=cfg();checks=[];before=json.loads(w.command(w.TS+['serve','status','--json']).stdout)
 if '1446' in before.get('TCP',{}):raise RuntimeError('Test requires unused writer route')
 s=w.ts_status();own=s['Self'];name=own['DNSName'].rstrip('.');device={'id':own['ID'],'name':'acceptance-self','os':'linux','online':True,'ips':own['TailscaleIPs']}
 writer=w.Writer(c,audit);listener=None;configured=False;filename='nas-writer-check-'+uuid.uuid4().hex+'.txt';target=Path(c['data'])/'shared'/filename
 fixture=Path(c['host'])/'state'/('writer-smb-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'));fixture.mkdir(mode=0o700);payload=fixture/'payload';payload.write_bytes(b'NAS unique writer acceptance\n')
 password=(Path(c['host'])/'secrets/smb-password').read_text().strip();image=json.loads(w.command(w.DOCKER+['image','inspect','nas/services:1']).stdout)[0]['Id']
 def tunnel(client):
  proc=subprocess.Popen(w.DOCKER+['exec','-i','nas-tailscale-1','tailscale','nc',name,'1446'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
  try:
   while True:
    ready,_,_=select.select([client,proc.stdout],[],[],15)
    if not ready:break
    for source in ready:
     data=client.recv(65536) if source is client else os.read(proc.stdout.fileno(),65536)
     if not data:return
     if source is client:proc.stdin.write(data);proc.stdin.flush()
     else:client.sendall(data)
  except (OSError,BrokenPipeError):pass
  finally:
   client.close();proc.kill();proc.communicate()
 def clients():
  while True:
   try:conn,_=listener.accept()
   except OSError:return
   threading.Thread(target=tunnel,args=(conn,),daemon=True).start()
 def smb(command,port):
  args=w.DOCKER+['run','--rm','-i','--network','host','--read-only','--tmpfs','/tmp','--mount','type=bind,src='+str(payload)+',dst=/fixture,readonly','--entrypoint','smbclient',image,'//127.0.0.1/shared','-p',str(port),'-A','/dev/stdin','-c',command]
  return subprocess.run(args,input='username = nas\npassword = '+password+'\n',text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45)
 try:
  with lock():
   writer.serve()
   w.command(w.TS+['serve','--bg','--yes','--tcp=1446','--proxy-protocol=2','tcp://127.0.0.1:1446']);configured=True
   listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(8);port=listener.getsockname()[1];threading.Thread(target=clients,daemon=True).start()
   with patch.object(w,'peers',return_value=[device]):writer.grant(device['id'])
   result=smb('put /fixture '+filename+'; get '+filename+' /tmp/readback',port)
   if result.returncode or not target.exists() or target.read_bytes()!=payload.read_bytes():raise RuntimeError('SMB write/read failed: '+result.stdout+result.stderr)
   checks.append('real Serve PROXY/WhoIs selected node uploads and downloads via SMB')
   # An established socket stays open until revoke, with actual SMB negotiate bytes relayed.
   held=socket.create_connection(('127.0.0.1',port));held.settimeout(8)
   deadline=time.monotonic()+10
   while not writer.snapshot()['connections']:
    if time.monotonic()>deadline:raise RuntimeError('Held gateway connection not established')
    time.sleep(.1)
   writer.revoke('acceptance-revoke');assert writer.active is None and writer.inspect() is None
   assert held.recv(1)==b'';held.close();checks.append('revocation drains actual gateway streams and removes entire SMB backend/handles')
   denied=smb('put /fixture '+filename,port)
   assert denied.returncode!=0 and target.read_bytes()==payload.read_bytes();checks.append('same node and same password cannot reconnect after revoke')
   # Give a different simulated selection, but let real WhoIs identify the self caller.
   writer.active={'id':'not-the-real-caller','name':'other'}
   denied=smb('put /fixture '+filename,port);assert denied.returncode!=0
   writer.active=None;checks.append('live WhoIs mismatch denied before SMB even with correct credentials')
 finally:
  writer.revoke('acceptance-cleanup')
  if configured:w.command(w.TS+['serve','--yes','--tcp=1446','off'])
  if listener:listener.close()
  if target.exists():
   if target.read_bytes()!=payload.read_bytes():raise RuntimeError('Refusing cleanup of changed acceptance file')
   target.unlink()
 after=json.loads(w.command(w.TS+['serve','status','--json']).stdout);assert before==after
 checks.append('existing read-only and private routes unchanged; own test content removed')
 result={'state':'PASS','checks':checks,'limitations':['Real NAS self identity only; actual Mac write and second client still pending','Revocation stream tested; client application unsaved edits remain user responsibility']}
 (fixture/'result.json').write_text(json.dumps(result,indent=2)+'\n');(Path(c['root'])/'system/runtime/acceptance/writer-smb.json').write_text(json.dumps(result,indent=2)+'\n');print('PASS:',len(checks),'real SMB identity/revocation checks',fixture)
if __name__=='__main__':main()
