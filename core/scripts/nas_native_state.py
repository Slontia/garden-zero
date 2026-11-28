"""Small mirrored cursors/owned-checkpoint registrations, never a file-tree index."""
import contextlib,fcntl,json
from pathlib import Path
from nas_backup_control import save
from nas_stream import IntegrityError

LIMIT=2*1024**2
class State:
 def __init__(self,c,snapshots):
  self.c=c;self.snapshots=snapshots;self.file=Path(c['host'])/'state/native-cloud.json';self.mirror=snapshots.root/'cursors.json'
 @contextlib.contextmanager
 def locked(self):
  with (Path(self.c['host'])/'state/native-cloud.lock').open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX);yield
 def read(self):
  candidates=[];present=False
  for path in (self.file,self.mirror):
   if path.is_symlink():raise IntegrityError('Linked native backup cursor refused')
   if not path.exists():continue
   present=True
   if path.stat().st_size>LIMIT:raise IntegrityError('Native cursor metadata exceeded its budget')
   try:value=json.loads(path.read_bytes())
   except ValueError:continue
   if value.get('format')!=1 or value.get('filesystem')!=self.c['data_storage']['uuid']:raise IntegrityError('Native cursor volume identity changed')
   candidates.append(value)
  if candidates:return max(candidates,key=lambda x:x['revision'])
  if present:raise IntegrityError('Both native cursor copies are damaged; refusing unregistered checkpoint cleanup')
  return {'format':1,'revision':0,'filesystem':self.c['data_storage']['uuid'],'accounts':{},'checkpoints':{},'drop_requested':False}
 def update(self,change):
  with self.locked():
   self.snapshots.verify(self.c);state=self.read();change(state);state['revision']+=1
   if len(json.dumps(state).encode())>LIMIT:raise IntegrityError('Native cursor metadata budget exhausted')
   save(self.file,state);save(self.mirror,state);return state
 def snapshot(self):
  with self.locked():return self.read()
 def account(self,ident,**changes):
  def mutate(state):state['accounts'].setdefault(ident,{}).update(changes)
  return self.update(mutate)['accounts'][ident]
 def checkpoint(self,ident,**changes):
  def mutate(state):state['checkpoints'].setdefault(ident,{}).update(changes)
  return self.update(mutate)['checkpoints'][ident]
