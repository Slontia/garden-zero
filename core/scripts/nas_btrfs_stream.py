"""Read-only Btrfs checkpoints and bounded-lifetime stream producers.

No formatting, writable application mounts, or arbitrary snapshot deletion. The
caller runs under the backup task's resource-limited service, not the NAS agent.
"""
import json,os,re,signal,subprocess,threading
from pathlib import Path
from nas_stream import identifier,IntegrityError

class ProcessStream:
 def __init__(self,args,*,check=lambda:None):
  self.check=check;self.error=bytearray();self.process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
  self.reader=threading.Thread(target=self.drain,daemon=True);self.reader.start();self.finished=False
 def drain(self):
  while block:=self.process.stderr.read(4096):
   if len(self.error)<65536:self.error.extend(block[:65536-len(self.error)])
 def read(self,n):self.check();return self.process.stdout.read(n)
 def finish(self):
  self.check();status=self.process.wait(timeout=120);self.reader.join(timeout=5)
  if status:raise RuntimeError('Backup stream producer failed; no recovery point published')
  self.finished=True
 def close(self):
  if self.process.poll() is None:
   os.killpg(self.process.pid,signal.SIGTERM)
   try:self.process.wait(timeout=5)
   except subprocess.TimeoutExpired:os.killpg(self.process.pid,signal.SIGKILL);self.process.wait(timeout=10)
  self.process.stdout.close();self.reader.join(timeout=5);self.process.stderr.close()
 def __enter__(self):return self
 def __exit__(self,*error):self.close()

def subvolume_info(directory):
 text=subprocess.check_output(['btrfs','subvolume','show',str(directory)],text=True,env={**os.environ,'LC_ALL':'C'})
 return dict((line.strip().split(':',1)[0],line.strip().split(':',1)[1].strip()) for line in text.splitlines() if ':' in line)

class Snapshots:
 def __init__(self,c,*,verify=None):
  if verify is None:
   from nas_storage import verify
  self.c=c;self.verify=verify;verify(c)
  self.mount=Path(c['data_storage']['mount']);self.root=self.mount/'.garden-zero-streams'
  if self.root.is_symlink():raise IntegrityError('Linked checkpoint root refused')
  self.root.mkdir(mode=0o700,exist_ok=True);marker=self.root/'owner.json';expected={'format':1,'filesystem':c['data_storage']['uuid'],'source':subvolume_info(c['data'])['UUID']}
  if marker.exists():
   if marker.is_symlink() or json.loads(marker.read_text())!=expected:raise IntegrityError('Checkpoint ownership mismatch')
  else:
   if any(self.root.iterdir()):raise IntegrityError('Unknown nonempty checkpoint root')
   fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
   with os.fdopen(fd,'w') as f:json.dump(expected,f);f.flush();os.fsync(f.fileno())
 def path(self,ident):return self.root/('s-'+identifier(ident))
 def metadata(self,ident):
  self.verify(self.c);p=self.path(ident)
  if p.is_symlink() or not p.is_dir() or p.stat().st_ino!=256:raise IntegrityError('Checkpoint is not a managed subvolume')
  readonly=subprocess.check_output(['btrfs','property','get','-ts',str(p),'ro'],text=True).strip()
  if readonly!='ro=true':raise IntegrityError('Send checkpoint must remain read-only')
  fields=subvolume_info(p)
  uuid=fields.get('UUID','')
  if not re.fullmatch(r'[a-f0-9-]{36}',uuid):raise IntegrityError('Missing checkpoint UUID')
  return {'snapshot_uuid':uuid,'creation_generation':int(fields['Gen at creation']),'source_uuid':fields['Parent UUID'],'filesystem_uuid':self.c['data_storage']['uuid'],'protocol':1,'kernel':os.uname().release,'tools':subprocess.check_output(['btrfs','--version'],text=True).strip()}
 def source(self):
  self.verify(self.c)
  nested=subprocess.check_output(['btrfs','subvolume','list','-o',self.c['data']],text=True)
  if nested.strip():raise IntegrityError('Nested data subvolumes require explicit resource registration; refusing to omit them')
  fields=subvolume_info(self.c['data']);owner=json.loads((self.root/'owner.json').read_text())
  if fields['UUID']!=owner['source']:raise IntegrityError('Active data subvolume identity changed')
  return {'uuid':fields['UUID'],'generation':int(fields['Generation'])}
 def create(self,ident,*,qgroup=None):
  self.verify(self.c);p=self.path(ident)
  if p.exists():raise IntegrityError('Checkpoint identifier already exists')
  args=['btrfs','subvolume','snapshot','-r']
  if qgroup:
   if not re.fullmatch(r'1/[1-9][0-9]{0,15}',qgroup):raise IntegrityError('Invalid checkpoint accounting group')
   args+=['-i',qgroup]
  subprocess.run(args+[self.c['data'],str(p)],stdout=subprocess.DEVNULL,check=True)
  return self.metadata(ident)
 def send(self,ident,parent=None,*,check=lambda:None):
  identity=self.metadata(ident);args=['nice','-n','19','ionice','-c','3','btrfs','send','--proto','1']
  if parent:
   identity['parent_uuid']=self.metadata(parent)['snapshot_uuid'];args+=['-p',str(self.path(parent))]
  return ProcessStream(args+[str(self.path(ident))],check=check),identity
 def discard(self,ident,expected_uuid):
  # Only the specifically registered immutable checkpoint may be released.
  if self.metadata(ident)['snapshot_uuid']!=expected_uuid:raise IntegrityError('Refusing to delete a changed checkpoint')
  subprocess.run(['btrfs','subvolume','delete',str(self.path(ident))],stdout=subprocess.DEVNULL,check=True)
