"""Observe retained CoW blocks; never impose a hard quota on application writes."""
import hashlib,json,re,shutil,subprocess
from pathlib import Path
from nas_stream import IntegrityError

class BudgetExceeded(RuntimeError):pass

class Budget:
 def __init__(self,snapshots,*,old_blocks=4*1024**3,minimum_free=16*1024**3):
  if old_blocks<64*1024**2 or minimum_free<64*1024**2:raise ValueError('Unsafe checkpoint space budget')
  self.snapshots=snapshots;self.limit=old_blocks;self.minimum=minimum_free
  self.path=snapshots.root/'budget.json';self.group=None;self.active_group=None;self.prepared=False
  if self.path.exists():
   if self.path.is_symlink():raise IntegrityError('Linked snapshot budget registration refused')
   state=json.loads(self.path.read_text())
   if state.get('filesystem')!=snapshots.c['data_storage']['uuid']:raise IntegrityError('Snapshot budget filesystem identity changed')
   self.group=state['qgroup'];self.active_group=state['active_group'];self.prepared=state.get('state','ready')=='ready'
   if not re.fullmatch(r'1/[1-9][0-9]{0,15}',self.group):raise IntegrityError('Invalid registered snapshot accounting group')
 def show(self,*,reconciling=False):
  self.snapshots.verify(self.snapshots.c)
  result=subprocess.run(['btrfs','qgroup','show','--raw',str(self.snapshots.mount)],capture_output=True,text=True,env={**__import__('os').environ,'LC_ALL':'C'})
  if result.returncode:raise BudgetExceeded('Snapshot accounting is unavailable; refuse unbudgeted checkpoints')
  if not reconciling and ('inconsistent' in result.stderr.lower() or 'rescan' in result.stderr.lower()):raise BudgetExceeded('Snapshot accounting requires reconciliation: '+result.stderr.strip()[:1000])
  rows={}
  for line in result.stdout.splitlines():
   parts=line.split()
   if parts and re.fullmatch(r'[0-9]+/[0-9]+',parts[0]):rows[parts[0]]={'referenced':int(parts[1]),'exclusive':int(parts[2])}
  return rows
 def prepare(self):
  """One-time setup only. The caller schedules this outside normal commits."""
  from nas_backup_control import save
  if self.group and self.prepared:
   if self.group not in self.show():raise IntegrityError('Registered accounting group disappeared')
   return
  self.snapshots.verify(self.snapshots.c)
  probe=subprocess.run(['btrfs','qgroup','show','--raw',str(self.snapshots.mount)],capture_output=True,text=True,env={**__import__('os').environ,'LC_ALL':'C'})
  if probe.returncode:
   if 'quotas not enabled' not in probe.stderr.lower():raise BudgetExceeded('Cannot determine filesystem quota accounting state')
   subprocess.run(['btrfs','quota','enable',str(self.snapshots.mount)],check=True)
   subprocess.run(['btrfs','quota','rescan','-W',str(self.snapshots.mount)],check=True)
  from nas_btrfs_stream import subvolume_info
  active='0/'+str(int(subvolume_info(self.snapshots.c['data'])['Subvolume ID']))
  number=int(hashlib.sha256(self.snapshots.c['data_storage']['uuid'].encode()).hexdigest()[:12],16)+1;group='1/'+str(number)
  if self.group:
   if self.group!=group or self.active_group!=active:raise IntegrityError('Preparing accounting intent no longer matches active source')
  else:
   if group in self.show():raise IntegrityError('Unknown existing checkpoint accounting group; refusing adoption')
   # Persist ownership BEFORE creating the group, so interrupted setup resumes
   # only our own group, never an unregistered pre-existing group.
   save(self.path,{'format':1,'state':'preparing','filesystem':self.snapshots.c['data_storage']['uuid'],'qgroup':group,'active_group':active})
   self.group=group;self.active_group=active
  subprocess.run(['btrfs','quota','rescan','-W',str(self.snapshots.mount)],check=True)
  if group not in self.show(reconciling=True):subprocess.run(['btrfs','qgroup','create',group,str(self.snapshots.mount)],check=True)
  parents=subprocess.check_output(['btrfs','qgroup','show','--raw','-p',str(self.snapshots.mount)],text=True,stderr=subprocess.DEVNULL)
  members=next((line.split()[3:] for line in parents.splitlines() if line.split() and line.split()[0]==active),[])
  if group not in ','.join(members).split(','):subprocess.run(['btrfs','qgroup','assign','--no-rescan',active,group,str(self.snapshots.mount)],check=True)
  # Setup-only rescan; no hard limits on live application writes.
  subprocess.run(['btrfs','quota','rescan','-w',str(self.snapshots.mount)],check=True)
  save(self.path,{'format':1,'state':'ready','filesystem':self.snapshots.c['data_storage']['uuid'],'qgroup':group,'active_group':active});self.prepared=True
 def check(self):
  if not self.group or not self.prepared:raise BudgetExceeded('Snapshot accounting is not prepared')
  rows=self.show();row=rows.get(self.group);active=rows.get(self.active_group)
  if row is None or active is None:raise BudgetExceeded('Snapshot accounting group missing')
  free=shutil.disk_usage(self.snapshots.mount).free
  old_bytes=max(0,row['referenced']-active['referenced'])
  if old_bytes>self.limit or free<self.minimum:raise BudgetExceeded('Checkpoint old-block/free-space budget exceeded; release local backup references and rebuild later')
  return {**row,'active_referenced':active['referenced'],'old_bytes':old_bytes,'free':free,'old_block_budget':self.limit,'minimum_free':self.minimum}
