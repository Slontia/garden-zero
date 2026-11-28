"""Btrfs/physical-WAL/config resources over a provider-independent object store.

Only invoked by the isolated native backup worker. Never stop an application to
capture a resource, never pass an application ID through conditional branches.
"""
import datetime,hashlib,io,json,os,secrets,shutil,tempfile,time
from pathlib import Path
from nas_backup_control import check,save
from nas_btrfs_stream import Snapshots
from nas_snapshot_budget import Budget,BudgetExceeded
from nas_native_state import State
from nas_objects import RcloneStore
from nas_stream import Repository,Journal,IntegrityError

from nas_native_config import ENGINE,enabled

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()

def password(c):
 p=Path(c['host'])/'secrets/backup-password'
 if p.is_symlink():raise IntegrityError('Linked recovery password refused')
 value=p.read_bytes().strip()
 if len(value)<32:raise IntegrityError('Native backup requires the existing strong recovery password')
 return value

class Manager:
 def __init__(self,c,*,capacity_check=True):
  from nasctl import volume
  from nas_storage import verify
  (volume if capacity_check else verify)(c);self.c=c;self.snapshots=Snapshots(c);self.state=State(c,self.snapshots)
  self.budget=Budget(self.snapshots,old_blocks=int(c.get('native_backup',{}).get('old_block_budget',4*1024**3)),minimum_free=c['critical_outer_free']+4*1024**3)
  self.last_check=0;self.current=None
 def guard(self):
  check()
  if time.monotonic()-self.last_check<2:return
  self.last_check=time.monotonic()
  if self.state.snapshot().get('drop_requested'):raise BudgetExceeded('Local checkpoint reclamation requested; backup paused')
  self.budget.check()
  floor=self.c.get('critical_image_outer_free',self.c['critical_outer_free'])
  if shutil.disk_usage(Path(self.c['image']).parent).free<floor+128*1024**2:raise BudgetExceeded('Internal-disk safety reserve is needed; pause backup before the application capacity guard')
 def prepare(self):
  from nas_pg_backup import prepare
  self.budget.prepare();prepare(self.c,apply=True)
  return {'engine':ENGINE,'state':'prepared','filesystem':self.c['data_storage']['uuid']}
 def repo(self,account,store):
  from nas_cloud import update_account
  registration=account.get('native_repository')
  if not registration:
   if not store.empty():raise IntegrityError('Native cloud namespace already exists with unknown ownership')
   registration={'id':secrets.token_hex(16),'state':'initializing','format':1}
   update_account(self.c,account['id'],native_repository=registration)
  repo=Repository(store,password(self.c),expected=registration['id'],initialize=registration['state']=='initializing')
  if registration['state']!='ready':
   registration={**registration,'state':'ready'};update_account(self.c,account['id'],native_repository=registration)
  return repo
 def checkpoint(self):
  self.guard();source=self.snapshots.source();state=self.state.snapshot();latest=state.get('latest')
  if latest and latest['source_uuid']==source['uuid'] and source['generation']<=latest['creation_generation']:
   ident=latest['id']
   if self.snapshots.path(ident).exists():
    metadata=self.snapshots.metadata(ident)
    if metadata['snapshot_uuid']!=latest['snapshot_uuid']:raise IntegrityError('Registered current checkpoint UUID changed')
    return ident,metadata
  if len(state['checkpoints'])>=8:raise BudgetExceeded('Checkpoint count budget reached; reclaim stale references before another capture')
  ident=secrets.token_hex(16);self.state.checkpoint(ident,state='creating',created=now())
  metadata=self.snapshots.create(ident,qgroup=self.budget.group);self.state.checkpoint(ident,state='ready',**metadata)
  self.state.update(lambda s:s.update(latest={'id':ident,**metadata}));return ident,metadata
 def data(self,account,repo,journal):
  ident=account['id'];local=self.state.snapshot()['accounts'].get(ident,{})
  pending=local.get('data_pending')
  if pending and pending.get('identity'):
   manifest=repo.recover('data',pending['id'],journal,producer=pending['identity'],parent=pending['parent'],minimum=local.get('data_sequence',0),check=self.guard)
   if manifest:
    self.state.account(ident,data_pending=None,data_parent=manifest['id'],data_sequence=manifest['sequence'],data_success=now());return manifest
  head=repo.head('data',local.get('data_sequence',0));current=self.snapshots.source();state=self.state.snapshot();latest=state.get('latest')
  if head:
   manifest=repo.manifest(head['commit']);producer=manifest['producer']
   # A shared latest checkpoint prevents two cloud accounts from triggering
   # one another's snapshot-only generation changes forever.
   watermark=latest['creation_generation'] if latest and latest['id']==head['commit'] else producer['creation_generation']
   if producer['source_uuid']==current['uuid'] and current['generation']<=watermark:
    self.state.account(ident,data_pending=None,data_parent=head['commit'],data_sequence=head['sequence']);return manifest
  pending=local.get('data_pending')
  if pending and self.snapshots.path(pending['id']).exists():point=pending['id']
  else:
   if pending:journal.finish(pending['id'])
   point,_=self.checkpoint();parent=head['commit'] if head and self.snapshots.path(head['commit']).exists() else None
   pending={'id':point,'parent':parent};self.state.account(ident,data_pending=pending)
  parent=pending['parent']
  if parent and not self.snapshots.path(parent).exists():raise IntegrityError('Pending incremental parent disappeared; reclaim and rebase before retry')
  if head and parent and repo.manifest(parent)['producer']['snapshot_uuid']!=self.snapshots.metadata(parent)['snapshot_uuid']:raise IntegrityError('Local/remote parent UUID mismatch')
  producer,identity=self.snapshots.send(point,parent,check=self.guard)
  with producer:
   pending={**pending,'identity':identity};self.state.account(ident,data_pending=pending)
   manifest=repo.upload('data',point,producer,journal,producer=identity,parent=parent,minimum=local.get('data_sequence',0),finish=producer.finish,check=self.guard)
  self.state.account(ident,data_pending=None,data_parent=point,data_sequence=manifest['sequence'],data_success=now());return manifest
 def database(self,account,repo,journal):
  from nas_pg_backup import Client
  from nas_pg_stream import lsn,Gap
  ident=account['id'];local=self.state.snapshot()['accounts'].get(ident,{});client=Client(self.c);identity=client.identity();pending=local.get('database_pending')
  if pending:
   manifest=repo.recover('database',pending['id'],journal,producer=pending['identity'],parent=pending['parent'],minimum=local.get('database_sequence',0),check=self.guard)
   if manifest:
    self.state.account(ident,database_pending=None,database_force_base=False,database_sequence=manifest['sequence'],database_checked_at=time.time(),database_success=now());return manifest
  head=repo.head('database',local.get('database_sequence',0));factory=None;parent=None
  if pending and head and head['commit']==pending['id']:
   manifest=repo.manifest(head['commit'])
   if manifest['producer']!=pending['identity'] or manifest['parent']!=pending['parent']:raise IntegrityError('Committed database intent changed')
   self.state.account(ident,database_pending=None,database_force_base=False,database_sequence=head['sequence'],database_checked_at=time.time(),database_success=now());return manifest
  if pending and pending['identity']['kind']=='postgres-base':
   journal.finish(pending['id']);self.state.account(ident,database_pending=None);pending=None
  if head and not local.get('database_force_base'):
   previous=repo.manifest(head['commit']);tag=previous['producer'];result=previous.get('result') or {}
   if tag.get('system')==identity['system'] and tag.get('timeline')==identity['timeline'] and tag.get('image')==identity['image']:
    start=tag.get('end_lsn',result.get('end_lsn'));end=lsn(identity['flush_lsn'])
    if start is not None and start>=end:
     self.state.account(ident,database_pending=None,database_sequence=head['sequence']);return previous
    if start is not None and not pending and time.time()-local.get('database_checked_at',0)<300:return previous
    if pending:
     tag=pending['identity'];point=pending['id'];parent=pending['parent'];start=tag['start_lsn'];end=tag['end_lsn']
     if tag['system']!=identity['system'] or tag['timeline']!=identity['timeline']:raise Gap('Pending WAL timeline changed; new baseline required')
    elif start is not None:point=secrets.token_hex(16);parent=head['commit'];tag={'kind':'postgres-wal',**identity,'start_lsn':start,'end_lsn':end}
    if start is not None:factory=lambda:client.wal(start,end,identity,check=self.guard)
  if factory is None:
   floor=self.c.get('critical_image_outer_free',self.c['critical_outer_free'])
   if shutil.disk_usage(Path(self.c['image']).parent).free<floor+256*1024**2:raise BudgetExceeded('Internal reserve insufficient for an online physical base')
   point=secrets.token_hex(16);tag={'kind':'postgres-base',**identity,'attempt':point};factory=lambda:client.base(point)
  self.state.account(ident,database_pending={'id':point,'identity':tag,'parent':parent})
  try:
   with factory() as producer:manifest=repo.upload('database',point,producer,journal,producer=tag,parent=parent,minimum=local.get('database_sequence',0),finish=producer.finish,check=self.guard)
  except Gap:
   journal.finish(point);self.state.account(ident,database_pending=None,database_force_base=True);raise
  self.state.account(ident,database_pending=None,database_force_base=False,database_sequence=manifest['sequence'],database_checked_at=time.time(),database_success=now());return manifest
 def configuration(self,account,repo,journal):
  from nas_continuous import inventory
  from nas_backup_config import archive_config
  ident=account['id'];local=self.state.snapshot()['accounts'].get(ident,{})
  pending=local.get('configuration_pending')
  if pending:
   manifest=repo.recover('configuration',pending['id'],journal,producer=pending['identity'],minimum=local.get('configuration_sequence',0),check=self.guard)
   if manifest:
    self.state.account(ident,configuration_pending=None,configuration_sequence=manifest['sequence'],configuration_signature=pending['identity']['fingerprint'],configuration_checked_at=time.time(),configuration_success=now());return manifest
  if pending:journal.finish(pending['id']);self.state.account(ident,configuration_pending=None)
  signature=hashlib.sha256(json.dumps({name:inventory(Path(self.c['host'])/name) for name in ('config','secrets')},sort_keys=True).encode()).hexdigest()
  head=repo.head('configuration',local.get('configuration_sequence',0))
  if head and signature==local.get('configuration_signature') and time.time()-local.get('configuration_checked_at',0)<3600:return repo.manifest(head['commit'])
  # The worker's /tmp is a bounded RAM filesystem. No complete user data or
  # physical database is ever spooled here or onto the internal disk.
  with tempfile.TemporaryDirectory(prefix='native-config-') as directory:
   archive=Path(directory)/'configuration.tar';archive_config(self.c,archive)
   if archive.stat().st_size>64*1024**2:raise BudgetExceeded('Configuration archive exceeds its explicit buffer budget')
   point=secrets.token_hex(16);identity={'kind':'configuration','fingerprint':signature,'captured_at':now()}
   self.state.account(ident,configuration_pending={'id':point,'identity':identity})
   with archive.open('rb') as source:manifest=repo.upload('configuration',point,source,journal,producer=identity,minimum=local.get('configuration_sequence',0),check=self.guard)
  self.state.account(ident,configuration_pending=None,configuration_sequence=manifest['sequence'],configuration_signature=signature,configuration_checked_at=time.time(),configuration_success=now());return manifest
 def collect(self):
  state=self.state.snapshot();referenced={state['latest']['id']} if state.get('latest') else set()
  for account in state['accounts'].values():
   if account.get('data_parent'):referenced.add(account['data_parent'])
   if account.get('data_pending'):referenced.add(account['data_pending']['id'])
  for ident,point in state['checkpoints'].items():
   if ident in referenced or point.get('state')!='ready':continue
   if self.snapshots.path(ident).exists():self.snapshots.discard(ident,point['snapshot_uuid'])
   self.state.update(lambda s,i=ident:s['checkpoints'].pop(i,None))
 def backup(self,account):
  from nas_cloud import update_account,event
  self.guard();ident=account['id'];previous=account.get('backup',{});record={**previous,'state':'running','error':None,'finished':None,'engine':ENGINE,'phase':'uploading','started':now(),'last_success':previous.get('last_success'),'consistency':'eventual'}
  update_account(self.c,ident,backup=record)
  try:
   with RcloneStore(self.c,account) as store:
    repo=self.repo(account,store);journal=Journal(Path(self.c['host'])/'state/native-chunks.sqlite',namespace=repo.id)
    try:
     results={}
     for name,capture in [('data',self.data),('database',self.database),('configuration',self.configuration)]:
      self.guard();manifest=capture(account,repo,journal);results[name]={'commit':manifest['id'],'sequence':manifest['sequence'],'bytes':manifest['bytes']}
     record.update(state='success',phase='complete',last_success=now(),finished=now(),resources=results,last_error=None,last_error_at=None)
    finally:journal.close()
   event(self.c,'native-backup-success',ident,resources=record['resources']);return record
  except BaseException as error:
   from nas_backup_control import Interrupted
   record.update(state='interrupted' if isinstance(error,Interrupted) else 'failed',finished=now(),error=str(error),last_error=str(error),last_error_at=now())
   event(self.c,'native-backup-failed',ident,error=str(error));raise
  finally:update_account(self.c,ident,backup=record);self.collect()
