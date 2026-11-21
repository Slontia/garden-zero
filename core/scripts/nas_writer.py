"""Single-device SMB gateway. Local host/root is trusted; remote passwords are not device IDs.

Only authenticated Serve PROXY v2 + live WhoIs may reach the ephemeral backend.
Authorization is memory-only. Revoke closes both sides before stopping the entire backend.
"""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import contextlib,datetime,ipaddress,json,os,select,socket,stat,subprocess,threading,time
from pathlib import Path
from nas_identity import address_header,node_identity
DOCKER=['/usr/bin/docker','--host','unix:///var/run/docker.sock']
TS=DOCKER+['exec','nas-tailscale-1','tailscale']
NAME='nas-file-writer';LABEL='org.nas.role=file-writer'
GATEWAY=1446;BACKEND=1447

def command(args,**kw):
 return subprocess.run(args,check=True,text=True,timeout=45,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**kw)
def ts_status():return json.loads(command(TS+['status','--json']).stdout)
def peers():
 s=ts_status()
 if s.get('BackendState')!='Running':raise RuntimeError('私人远程连接不可用')
 result=[]
 for n in s.get('Peer',{}).values():
  if not n.get('ID') or n.get('InNetworkMap') is not True or n.get('Expired') or not n.get('TailscaleIPs'):continue
  result.append({'id':n['ID'],'name':n.get('HostName') or n.get('DNSName','').rstrip('.'),'os':n.get('OS',''),'online':bool(n.get('Online')),'ips':n['TailscaleIPs']})
 return result

def verified_identity(source,whois,status):
 identity=whois.get('Node',{}).get('StableID')
 peer=next((p for p in status.get('Peer',{}).values() if p.get('ID')==identity),None)
 if peer is None and identity!=status.get('Self',{}).get('ID'):raise ValueError('Identity missing from live network map')
 return node_identity(source,whois,peer)

def safe_root(c):
 root=Path(c['data'])/'shared'
 if root.is_symlink() or root.resolve()!=Path(c['data']).resolve()/'shared' or not root.is_dir():raise RuntimeError('普通文件目录身份不匹配')
 # Syncthing currently has no folders. Fail closed until a shared locking adapter exists.
 import xml.etree.ElementTree as ET
 if ET.parse(Path(c['host'])/'state/syncthing/config.xml').getroot().findall('folder'):raise RuntimeError('已配置同步目录，需先验证与写入目录互斥')
 for directory,dirs,files in os.walk(root,followlinks=False):
  for name in dirs+files:
   p=Path(directory)/name;st=p.lstat()
   if stat.S_ISLNK(st.st_mode) or (not stat.S_ISDIR(st.st_mode) and (not stat.S_ISREG(st.st_mode) or st.st_nlink!=1)):raise RuntimeError('写入目录含链接或特殊文件，拒绝开放')
 return root

class Writer:
 def __init__(self,c,audit):
  self.c=c;self.audit=audit;self.mutex=threading.RLock();self.active=None;self.sessions=set();self.epoch=0;self.error=None;self.listener=None;self.ready=False;self.slots=threading.BoundedSemaphore(32)
 def inspect(self):
  ids=command(DOCKER+['ps','-aq','--filter','name=^/'+NAME+'$']).stdout.split()
  if not ids:return None
  rows=json.loads(command(DOCKER+['inspect',NAME]).stdout)
  if rows[0]['Config'].get('Labels',{}).get('org.nas.role')!='file-writer':raise RuntimeError('写入后端名称被其他容器占用')
  return rows[0]
 def revoke(self,reason):
  with self.mutex:
   previous=self.active;self.active=None;self.epoch+=1;self.error=None
   for conn in list(self.sessions):
    with contextlib.suppress(OSError):conn.shutdown(socket.SHUT_RDWR)
    conn.close()
   self.sessions.clear()
   try:
    row=self.inspect()
    if row:
     if row['State']['Running']:command(DOCKER+['stop','--time','30',NAME])
     row=self.inspect()
     if row['State']['Running'] or row['State']['ExitCode']==137:raise RuntimeError('旧写入进程未正常结束；禁止新授权')
     command(DOCKER+['rm',NAME])
   except Exception as e:
    self.error=str(e);raise
   finally:
    self.audit(self.c,'writer-revoke',{'reason':reason,'previous':previous,'error':self.error})
 def snapshot(self):
  if self.c.get('file_access',{}).get('mode')=='full_sync':return {'mode':'full_sync','active_writer':None,'writer_selection':'disabled','write_roots':['shared']}
  with self.mutex:return {'mode':'single_writer' if self.active else 'read_only','active_writer':dict(self.active) if self.active else None,'writer_selection':'available' if self.ready else 'unavailable','write_roots':['shared'],'connections':len(self.sessions)//2,'error':self.error}
 def grant(self,identity):
  if self.c.get('file_access',{}).get('mode')=='full_sync':raise RuntimeError('共享空间采用多设备完整同步，已停用独占 SMB 写入')
  with self.mutex:
   # Revoke first even for an invalid/offline target: no stale privilege survives a failed switch.
   self.revoke('switch')
   try:
    if not self.ready:raise RuntimeError('写入网关未就绪')
    if (Path(self.c['host'])/'state/deployment-blocked').exists():raise RuntimeError('存储保护中，禁止写入')
    from nasctl import volume,inventory
    inv={r['service']:r for r in inventory(self.c)}
    if any(inv.get(m,{}).get('state')!='running' for m in ('smb','tailscale')):raise RuntimeError('请先开启 SMB 与私人远程连接模块')
    volume(self.c);root=safe_root(self.c)
    device=next((p for p in peers() if p['id']==identity and p['online']),None)
    if not device:raise RuntimeError('设备不在线或未登记')
    # Resolve through WhoIs now and again for every TCP connection.
    whois=json.loads(command(TS+['whois','--json',device['ips'][0]]).stdout)
    if verified_identity(device['ips'][0],whois,ts_status())!=identity:raise RuntimeError('设备身份核验失败')
    env={}
    for line in (Path(self.c['host'])/'config/compose.env').read_text().splitlines():
     if '=' in line:
      k,v=line.split('=',1);env[k]=v
    image=env.get('NAS_SERVICES_IMAGE','')
    if not image:raise RuntimeError('未找到可信本机文件服务镜像')
    trusted=json.loads(command(DOCKER+['image','inspect','nas/services:1']).stdout)[0]['Id']
    resolved=json.loads(command(DOCKER+['image','inspect',image]).stdout)[0]['Id']
    if trusted!=resolved:raise RuntimeError('文件服务镜像与本机固定版本不匹配')
    entry=Path(self.c['host'])/'installed/file-writer-entrypoint.py'
    args=DOCKER+['run','-d','--name',NAME,'--label',LABEL,'--restart=no','--network','host','--read-only','--security-opt','no-new-privileges','--memory','192m','--pids-limit','128','--tmpfs','/run:rw,nosuid,nodev,size=32m','--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=8m']
    for source,target,ro in [(root,'/write',False),(Path(self.c['host'])/'secrets/smb-password','/credentials/smb-password',True),(entry,'/app/file-writer.py',True)]:
     if ',' in str(source):raise RuntimeError('Unsupported mount path')
     args+=['--mount','type=bind,src='+str(source)+',dst='+target+(',readonly' if ro else '')]
    command(args+['--entrypoint','/usr/local/bin/python',image,'/app/file-writer.py'])
    deadline=time.monotonic()+20
    while True:
     if not self.inspect()['State']['Running']:raise RuntimeError('写入后端启动失败')
     try:
      with socket.create_connection(('127.0.0.1',BACKEND),timeout=1):break
     except OSError:
      if time.monotonic()>deadline:raise RuntimeError('写入后端未就绪')
      time.sleep(.3)
    self.active=device;self.audit(self.c,'writer-grant',{'device':device,'root':'shared'})
   except Exception:
    self.revoke('grant-failed');raise
 def serve(self):
  self.revoke('agent-start')
  if self.c.get('file_access',{}).get('mode')=='full_sync':return
  listener=socket.socket();listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);listener.bind(('127.0.0.1',GATEWAY));listener.listen(32);self.listener=listener;self.ready=True
  def loop():
   try:
    while True:
     conn,peer=listener.accept()
     with self.mutex:
      if not self.active or not self.slots.acquire(blocking=False):conn.close();continue
      epoch=self.epoch
     threading.Thread(target=self.handle,args=(conn,peer,epoch),daemon=True).start()
   except Exception:
    self.ready=False;self.revoke('gateway-failure')
  threading.Thread(target=loop,daemon=True).start()
 def handle(self,conn,peer,epoch):
  backend=None
  try:
   conn.settimeout(8)
   if peer[0]!='127.0.0.1':raise ValueError('Untrusted gateway transport')
   def exact(size):
    data=b''
    while len(data)<size:
     chunk=conn.recv(size-len(data))
     if not chunk:raise ValueError('Truncated header')
     data+=chunk
    return data
   header=exact(16);size=int.from_bytes(header[14:16],'big')
   if size>512:raise ValueError('Oversized header')
   parsed=address_header(header,exact(size))
   s=ts_status()
   if s.get('BackendState')!='Running' or parsed['destination'] not in s['Self']['TailscaleIPs'] or parsed['destination_port']!=GATEWAY:raise ValueError('Wrong proxy destination')
   whois=json.loads(command(TS+['whois','--json',parsed['source']]).stdout);identity=verified_identity(parsed['source'],whois,s)
   with self.mutex:
    if not self.ready or epoch!=self.epoch or not self.active or identity!=self.active['id']:raise PermissionError('Device is not selected')
    backend=socket.create_connection(('127.0.0.1',BACKEND),timeout=5)
    self.sessions.update((conn,backend))
   conn.settimeout(None);backend.settimeout(None)
   # Backpressure bounds each relay to one buffer; no half-close survives revocation.
   while True:
    readable,_,_=select.select([conn,backend],[],[],30)
    for source in readable:
     data=source.recv(65536)
     if not data:return
     (backend if source is conn else conn).sendall(data)
    with self.mutex:
     if epoch!=self.epoch:return
  except Exception:pass
  finally:
   for sock in (conn,backend):
    if sock:
     with contextlib.suppress(OSError):sock.shutdown(socket.SHUT_RDWR)
     sock.close()
     with self.mutex:self.sessions.discard(sock)
   self.slots.release()
 def monitor(self):
  # Called by the agent scheduler; identity loss never transfers permission.
  with self.mutex:
   if not self.active:return
   try:
    from nasctl import volume
    volume(self.c);safe_root(self.c)
    if not any(p['id']==self.active['id'] and p['online'] for p in peers()):raise RuntimeError('写入设备离线')
    if not self.inspect()['State']['Running']:raise RuntimeError('写入后端已停止')
   except Exception:self.revoke('health-or-identity-loss')
