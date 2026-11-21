"""Authenticated incremental mirror of immutable backup sets; no live database copies."""
import sys
sys.dont_write_bytecode=True
import contextlib,hashlib,hmac,json,os,re,shutil,stat,tarfile,time,uuid
from pathlib import Path,PurePosixPath
META='.garden-zero-backup'

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def digest_file(path):
 h=hashlib.sha256()
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 with os.fdopen(fd,'rb') as stream:
  for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
 return h.hexdigest()
def unsafe(path):return path.is_symlink() or path.is_junction()
def relative(name):
 p=PurePosixPath(name)
 if not name or name!=p.as_posix() or p.is_absolute() or any(x in ('.','..') or '\\' in x or ':' in x for x in p.parts) or p.parts[0]==META:raise RuntimeError('Unsafe mirror member path')
 return name

def sync_directory(path):
 fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:os.fsync(fd)
 finally:os.close(fd)
def durable_tree(root):
 directories=[]
 for directory,children,files in os.walk(root,followlinks=False):
  path=Path(directory)
  if unsafe(path):raise RuntimeError('Linked directory during durable commit')
  directories.append(path)
 for path in reversed(directories):sync_directory(path)

def atomic(path,value):
 temp=path.with_name(path.name+'.tmp')
 if unsafe(temp):raise RuntimeError('Linked metadata temporary file')
 temp.unlink(missing_ok=True)
 fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as stream:stream.write(canonical(value));stream.flush();os.fsync(stream.fileno())
 os.replace(temp,path);sync_directory(path.parent)

class Snapshot:
 """Read only a COMPLETE, checksummed internal backup. Tar links are refused."""
 def __init__(self,path):
  self.path=Path(path);self.refs={};self.entries={};self.times={};self.tar=None
  if unsafe(self.path) or not (self.path/'COMPLETE').is_file():raise RuntimeError('Incomplete or linked source backup')
  manifest=json.loads((self.path/'manifest.json').read_text())
  if not re.fullmatch(r'\d{8}-\d{6}',manifest['time']) or manifest['time']!=self.path.name:raise RuntimeError('Invalid source snapshot identity')
  for name,expected in manifest['files'].items():
   if Path(name).name!=name or unsafe(self.path/name) or digest_file(self.path/name)!=expected:raise RuntimeError('Source backup checksum mismatch')
  if not {'database.dump','configuration.tar','user-content.tar'}<=set(manifest['files']):raise RuntimeError('Incomplete snapshot payload')
  self.id=manifest['time'];self.manifest_sha256=digest_file(self.path/'manifest.json')
  try:
   self.tar=tarfile.open(self.path/'user-content.tar','r:')
   for member in self.tar.getmembers():
    name=relative(member.name)
    if name!='data' and not name.startswith('data/'):raise RuntimeError('Unexpected content archive root')
    if name in self.entries:raise RuntimeError('Duplicate archive path')
    if member.isdir():self.entries[name]={'kind':'dir'}
    elif member.isfile():
     with self.tar.extractfile(member) as f:
      h=hashlib.sha256()
      for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
     self.entries[name]={'kind':'file','size':member.size,'sha256':h.hexdigest()};self.refs[name]=member;self.times[name]=member.mtime
    else:raise RuntimeError('Symlinks, hardlinks and special archive members are not mirrored')
   self.entries['recovery']={'kind':'dir'}
   for name in ('database.dump','configuration.tar','manifest.json','COMPLETE'):
    p=self.path/name;key='recovery/'+name;self.entries[key]={'kind':'file','size':p.stat().st_size,'sha256':digest_file(p)};self.refs[key]=p;self.times[key]=p.stat().st_mtime
   folded=set()
   for name in self.entries:
    if name.casefold() in folded:raise RuntimeError('Case-colliding snapshot paths')
    folded.add(name.casefold())
    for parent in PurePosixPath(name).parents:
     if str(parent)!='.' and self.entries.get(str(parent))!={'kind':'dir'}:raise RuntimeError('Missing snapshot parent directory')
  except BaseException:
   self.close();raise
 def open(self,name):
  ref=self.refs[name]
  return ref.open('rb') if isinstance(ref,Path) else self.tar.extractfile(ref)
 def close(self):
  if self.tar:self.tar.close()
 def __enter__(self):return self
 def __exit__(self,*args):self.close()

class Mirror:
 """Target manifests/journals are HMAC signed; the trusted checkpoint stays off-target."""
 def __init__(self,root,checkpoint,key,identity,failpoint=None):
  self.root=Path(root);self.meta=self.root/META;self.checkpoint=Path(checkpoint);self.key=key;self.identity=identity;self.failpoint=failpoint or (lambda event:None);self.cache={}
  if not self.root.is_dir() or unsafe(self.root):raise RuntimeError('Mirror root missing or linked')
 def envelope(self,payload):return {'payload':payload,'hmac_sha256':hmac.new(self.key,canonical(payload),hashlib.sha256).hexdigest()}
 def read(self,path):
  if unsafe(path) or not path.is_file():raise RuntimeError('Missing or linked mirror metadata')
  value=json.loads(path.read_text());p=value['payload']
  if not hmac.compare_digest(value.get('hmac_sha256',''),self.envelope(p)['hmac_sha256']) or p.get('identity')!=self.identity:raise RuntimeError('Mirror metadata signature/identity mismatch')
  return p
 def write(self,path,payload):atomic(path,self.envelope(payload))
 def anchor(self,generation,manifest):
  self.write(self.checkpoint,{'identity':self.identity,'repository':manifest['repository'],'generation':generation,'manifest_sha256':hashlib.sha256(canonical(manifest)).hexdigest()})
 def ensure(self):
  if not self.meta.exists():
   if self.checkpoint.exists() or list(self.root.iterdir()):raise RuntimeError('Unregistered non-empty target or missing existing mirror; refusing adoption')
   self.meta.mkdir(mode=0o700)
   first={'identity':self.identity,'repository':uuid.uuid4().hex,'generation':0,'source':None,'entries':{}}
   self.write(self.meta/'manifest.json',first);self.anchor(0,first)
  if unsafe(self.meta) or not self.checkpoint.exists():raise RuntimeError('Untrusted target; registration checkpoint missing')
  allowed={'manifest.json','pending.json','stage','history','manifest.json.tmp','pending.json.tmp'}
  for p in self.meta.iterdir():
   if p.name not in allowed or unsafe(p):raise RuntimeError('Unexpected or linked metadata entry')
  manifest=self.read(self.meta/'manifest.json');anchor=self.read(self.checkpoint)
  if manifest['repository']!=anchor['repository']:raise RuntimeError('Foreign mirror repository')
  pending=self.read(self.meta/'pending.json') if (self.meta/'pending.json').exists() else None
  if pending:
   if pending['repository']!=manifest['repository'] or pending['generation']!=pending['base']['generation']+1:raise RuntimeError('Invalid interrupted transaction')
   old=pending['base'];new=pending['next']
   if manifest not in (old,new) or anchor['generation'] not in (old['generation'],new['generation']):raise RuntimeError('Replayed or inconsistent mirror transaction')
   expected=old if anchor['generation']==old['generation'] else new
   if anchor['manifest_sha256']!=hashlib.sha256(canonical(expected)).hexdigest():raise RuntimeError('Trusted checkpoint differs from journal')
  elif anchor['generation']!=manifest['generation'] or anchor['manifest_sha256']!=hashlib.sha256(canonical(manifest)).hexdigest():raise RuntimeError('Target does not match trusted last backup')
  return manifest,pending
 def scan(self,root=None):
  root=root or self.root;result={}
  def walk(parent):
   for p in parent.iterdir():
    if root==self.root and parent==root and p.name==META:continue
    name=relative(p.relative_to(root).as_posix())
    if unsafe(p):raise RuntimeError('Linked destination entry: '+name)
    st=p.stat()
    if stat.S_ISDIR(st.st_mode):result[name]={'kind':'dir'};walk(p)
    elif stat.S_ISREG(st.st_mode):
     fingerprint=(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)
     checksum=self.cache.get(fingerprint)
     if checksum is None:checksum=digest_file(p);self.cache[fingerprint]=checksum
     inode=(st.st_dev,st.st_ino);count,links=self.links.get(inode,(0,st.st_nlink));self.links[inode]=(count+1,max(links,st.st_nlink))
     result[name]={'kind':'file','size':st.st_size,'sha256':checksum}
    else:raise RuntimeError('Unsupported destination file: '+name)
  walk(root);return result
 def history_refs(self,manifest,pending):
  refs=dict(manifest.get('history',{}))
  if pending:
   refs.update(pending['base'].get('history',{}))
   if pending.get('archive_generation'):refs[pending['archive_generation']]=hashlib.sha256(canonical(pending['base'])).hexdigest()
  return refs
 def committed(self,pending):
  return bool(pending and self.read(self.checkpoint)['generation']==pending['generation'])
 def audit(self,manifest,pending):
  self.links={};actual=self.scan();self.validate(actual,manifest,pending)
  if pending and not pending.get('history_ready') and actual!=pending['base']['entries']:raise RuntimeError('Previous backup changed before history snapshot was secured')
  refs=self.history_refs(manifest,pending);history=self.meta/'history'
  if unsafe(history):raise RuntimeError('Linked history directory')
  found={p.name for p in history.iterdir()} if history.exists() else set()
  if found-set(refs):raise RuntimeError('Unexpected history snapshot')
  purging=bool(pending and pending.get('purge_history') and self.committed(pending))
  for name,expected in refs.items():
   partial=bool(pending and name==pending.get('archive_generation') and not pending.get('history_ready'))
   if name not in found:
    if partial or purging:continue
    raise RuntimeError('Missing historical backup')
   folder=history/name
   if unsafe(folder) or not folder.is_dir():raise RuntimeError('Linked or invalid historical backup')
   children={p.name for p in folder.iterdir()}
   if children-{'manifest.json','manifest.json.tmp','content'}:raise RuntimeError('Unexpected historical backup entry')
   record=folder/'manifest.json'
   if record.exists():
    payload=self.read(record)
    if hashlib.sha256(canonical(payload)).hexdigest()!=expected:raise RuntimeError('Historical manifest differs from trusted record')
   elif partial:payload=pending['base']
   elif purging and not children:continue
   else:raise RuntimeError('Historical manifest is missing')
   content=folder/'content'
   if unsafe(content):raise RuntimeError('Linked historical content')
   stored=self.scan(content) if content.exists() else {}
   if partial or purging:
    if any(value!=payload['entries'].get(key) for key,value in stored.items()):raise RuntimeError('Historical files were externally modified')
   elif stored!=payload['entries']:raise RuntimeError('Historical backup differs from its successful snapshot')
  if any(count!=links for count,links in self.links.values()):raise RuntimeError('File has links outside managed backup generations')
  return actual
 def make_history(self,pending):
  if pending.get('history_ready'):return
  name=pending.get('archive_generation')
  if name:
   folder=self.meta/'history'/name;content=folder/'content';content.mkdir(parents=True,exist_ok=True)
   for key,value in sorted(pending['base']['entries'].items(),key=lambda item:(item[0].count('/'),item[0])):
    path=content/key
    if unsafe(path):raise RuntimeError('Link appeared in history snapshot')
    if value['kind']=='dir':path.mkdir(exist_ok=True)
    elif not path.exists():
     source=self.root/key;self.parents(key)
     if digest_file(source)!=value['sha256']:raise RuntimeError('Previous content changed before history snapshot')
     os.link(source,path,follow_symlinks=False);self.failpoint('history-file')
   durable_tree(content);sync_directory(folder);sync_directory(folder.parent)
   self.write(folder/'manifest.json',pending['base'])
  pending['history_ready']=True;self.write(self.meta/'pending.json',pending);self.failpoint('history')
 def purge(self,manifest,pending):
  if not pending.get('purge_history'):return 0
  if not self.committed(pending):raise RuntimeError('Cannot purge before a new complete backup is committed')
  refs=self.history_refs(manifest,pending);history=self.meta/'history'
  for name in sorted(refs):
   self.audit(manifest,pending)
   folder=history/name
   if not folder.exists():continue
   content=folder/'content'
   if content.exists():
    paths=sorted(content.rglob('*'),key=lambda p:len(p.parts),reverse=True)
    for path in paths:
     if unsafe(path):raise RuntimeError('Linked history entry during purge')
     if path.is_dir():path.rmdir()
     else:path.unlink();self.failpoint('purged-file')
    content.rmdir()
   for basename in ['manifest.json.tmp','manifest.json']:
    path=folder/basename
    if unsafe(path):raise RuntimeError('Linked history metadata during purge')
    path.unlink(missing_ok=True)
   folder.rmdir()
  return len(refs)
 def validate(self,actual,manifest,pending):
  if not pending:
   if actual!=manifest['entries']:raise RuntimeError('Target files differ from the last successful backup; no changes made')
  else:
   old=pending['base']['entries'];new=pending['next']['entries']
   for name,value in actual.items():
    if value not in (old.get(name),new.get(name)):raise RuntimeError('Interrupted target contains external changes: '+name)
   for name in old.keys()&new.keys():
    if old[name]['kind']==new[name]['kind'] and name not in actual:raise RuntimeError('Interrupted target is missing a required file: '+name)
 def check(self):
  manifest,pending=self.ensure();self.audit(manifest,pending)
  return {'generation':manifest['generation'],'pending_source':pending['next']['source'] if pending else None,'source':manifest['source'],'files':sum(e['kind']=='file' for e in manifest['entries'].values()),'history_snapshots':len(manifest.get('history',{}))}
 def parents(self,name):
  for parent in (self.root/name).parents:
   if parent==self.root:break
   if unsafe(parent) or not parent.is_dir():raise RuntimeError('Destination parent changed')
 def sync(self,source,purge_history=False):
  manifest,pending=self.ensure();actual=self.audit(manifest,pending)
  if pending and (pending['next']['source']!=source.id or pending['next']['entries']!=source.entries):raise RuntimeError('Resume the previous snapshot before starting a new backup')
  if not pending:
   archive=('generation-%020d'%manifest['generation']) if manifest['generation'] else None
   history=dict(manifest.get('history',{}))
   if archive:history[archive]=hashlib.sha256(canonical(manifest)).hexdigest()
   following={'identity':self.identity,'repository':manifest['repository'],'generation':manifest['generation']+1,'source':source.id,'source_manifest_sha256':source.manifest_sha256,'entries':source.entries,'history':{} if purge_history else history}
   pending={'identity':self.identity,'repository':manifest['repository'],'generation':following['generation'],'base':manifest,'next':following,'archive_generation':archive,'history_ready':False,'purge_history':bool(purge_history)}
   self.write(self.meta/'pending.json',pending)
  desired=pending['next']['entries'];stage=self.meta/'stage';stage.mkdir(exist_ok=True)
  copies={name:e for name,e in desired.items() if e['kind']=='file' and actual.get(name)!=e}
  permitted={hashlib.sha256(name.encode()).hexdigest() for name,e in desired.items() if e['kind']=='file'}
  for p in stage.iterdir():
   if unsafe(p) or not p.is_file() or p.name not in permitted:raise RuntimeError('Unexpected staging entry')
  total=sum(e['size'] for e in copies.values())
  staged=sum(min(p.stat().st_size,p.stat().st_blocks*512) for p in stage.iterdir() if p.is_file() and p.stat().st_nlink==1)
  if shutil.disk_usage(self.root).free+staged<total+16*1024**2:raise RuntimeError('Insufficient mirror free space; existing files retained')
  written=0;reused=0;resumed=0
  for name,entry in copies.items():
   path=stage/hashlib.sha256(name.encode()).hexdigest();offset=0;valid=False
   with source.open(name) as src:
    if path.exists() and path.stat().st_nlink==1 and path.stat().st_size<=entry['size']:
     offset=path.stat().st_size;prefix=hashlib.sha256();remaining=offset
     while remaining:
      block=src.read(min(1024*1024,remaining))
      if not block:raise RuntimeError('Source truncated during resume verification')
      prefix.update(block);remaining-=len(block)
     valid=digest_file(path)==prefix.hexdigest()
    if valid:
     fd=os.open(path,os.O_WRONLY|os.O_APPEND|os.O_NOFOLLOW)
     if offset==entry['size']:reused+=1
     elif offset:resumed+=offset
    else:
     path.unlink(missing_ok=True);src.seek(0);offset=0
     fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'ab') as out:
     info=os.fstat(out.fileno())
     if info.st_nlink!=1 or info.st_size!=offset:raise RuntimeError('Staging file changed before resume')
     for block in iter(lambda:src.read(1024*1024),b''):
      out.write(block);written+=len(block);self.failpoint('copy-chunk')
     out.flush();os.fsync(out.fileno())
   if path.stat().st_size!=entry['size'] or digest_file(path)!=entry['sha256']:raise RuntimeError('Staged source content changed')
   if name in getattr(source,'times',{}):os.utime(path,(source.times[name],source.times[name]))
  self.failpoint('staged')
  # Revalidate after staging; target tampering must not authorize an overwrite/delete.
  actual=self.audit(manifest,pending)
  self.make_history(pending)
  actual=self.audit(manifest,pending)
  removed=0
  for name in sorted(actual,key=lambda x:(x.count('/'),x),reverse=True):
   if name not in desired or actual[name]['kind']!=desired[name]['kind']:
    path=self.root/name;self.parents(name)
    if unsafe(path):raise RuntimeError('Destination link appeared')
    if actual[name]['kind']=='dir':path.rmdir()
    else:
     if digest_file(path)!=actual[name]['sha256']:raise RuntimeError('Destination modified before deletion')
     path.unlink();removed+=1
    self.failpoint('deleted')
  for name,entry in sorted(desired.items(),key=lambda pair:(pair[0].count('/'),pair[0])):
   self.parents(name)
   path=self.root/name
   if unsafe(path):raise RuntimeError('Destination link appeared')
   if entry['kind']=='dir':path.mkdir(exist_ok=True)
   elif name in copies:
    if path.exists() and digest_file(path)!=actual.get(name,{}).get('sha256'):raise RuntimeError('Destination modified before replacement')
    os.replace(stage/hashlib.sha256(name.encode()).hexdigest(),path);self.failpoint('replaced')
  if self.audit(manifest,pending)!=desired:raise RuntimeError('Mirror final verification failed; journal retained')
  for path in self.root.iterdir():
   if path.name!=META and path.is_dir():durable_tree(path)
  sync_directory(self.root);sync_directory(stage)
  self.write(self.meta/'manifest.json',pending['next']);self.failpoint('manifest')
  self.anchor(pending['generation'],pending['next']);self.failpoint('checkpoint')
  for p in stage.iterdir():
   if p.name not in permitted or unsafe(p):raise RuntimeError('Unexpected staging cleanup entry')
   p.unlink()
  stage.rmdir();purged=self.purge(pending['next'],pending);(self.meta/'pending.json').unlink();sync_directory(self.meta)
  return {'generation':pending['generation'],'source':source.id,'copied_files':len(copies),'deleted_files':removed,'written_bytes':written,'reused_staged_files':reused,'resumed_bytes':resumed,'unchanged_files':sum(e['kind']=='file' for e in desired.values())-len(copies),'files':sum(e['kind']=='file' for e in desired.values()),'history_snapshots':len(pending['next'].get('history',{})),'purged_snapshots':purged,'deletion_policy':'purge_after_commit' if pending.get('purge_history') else 'retain_history','verification':'SHA-256; signed manifests; trusted checkpoint; complete previous generation retained until commit'}
