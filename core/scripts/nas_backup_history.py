"""Disk backup outcomes, retained independently of an occasionally disconnected target."""
import datetime,json,secrets,os,threading
from pathlib import Path
from nas_mirror import atomic

def disk(c):
 root=Path(c.get('hdd_root') or '/')
 relative='/'.join(root.parts[3:]) if root.parts[:2]==('/','mnt') else root.name
 known={}
 try:known=json.loads((Path(c['host'])/'state/hdd-volume.json').read_text())
 except (OSError,ValueError):pass
 label=known.get('name') if known.get('uid')==c.get('hdd_guid') else None
 return {'name':label or c.get('_backup_volume_label') or c.get('hdd_label') or '名称未记录','uid':c.get('hdd_guid') or '未知','relative_path':relative or '/'}

def begin(c,record,previous=None):
 directory=Path(c['host'])/'state/disk-backup-history';directory.mkdir(exist_ok=True)
 if previous and not any(directory.glob('*.json')):atomic(directory/'legacy.json',{**previous,'disk':disk(c),'legacy':True})
 path=directory/(datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+secrets.token_hex(6)+'.json')
 atomic(path,{**record,'disk':disk(c),'pid':os.getpid(),'process_start':Path('/proc/self/stat').read_text().split()[21]})
 return path

def finish(c,path,record):atomic(path,{**record,'disk':disk(c)})

def history(c,last):
 directory=Path(c['host'])/'state/disk-backup-history'
 rows=[]
 for path in sorted(directory.glob('*.json'),reverse=True):
  try:
   row=json.loads(path.read_text())
   if row.get('state')=='running':
    process=Path('/proc')/str(row.get('pid',0))/'stat'
    if not process.is_file() or process.read_text().split()[21]!=row.get('process_start'):row={**row,'state':'interrupted'}
   rows.append(row)
  except (OSError,ValueError):continue
 if not rows and last:rows=[{**last,'disk':disk(c),'legacy':True}]
 return sorted(rows,key=lambda row:row.get('started') or row.get('time') or '',reverse=True)

def preparation_failure(c,error):
 path=Path(c['host'])/'state/hdd-backup.json'
 previous=json.loads(path.read_text()) if path.is_file() else {}
 now=datetime.datetime.now(datetime.timezone.utc).isoformat()
 record={'operation':'backup','state':'interrupted' if isinstance(error,(InterruptedError,KeyboardInterrupt)) else 'failed','started':now,'time':now,'last_success':previous.get('last_success'),'error':'准备备份未完成：'+str(error)}
 entry=begin(c,record,previous);finish(c,entry,record);atomic(path,record)

def cloud_record(c,account,record=None,history_id=None):
 """One durable row per target and job, including capture failures."""
 from nas_backup_control import ACTIVE
 active=ACTIVE.get();job=active.row if active else {}
 ident=job.get('id') or secrets.token_hex(16)
 account_id=account['id']
 if not account_id.replace('-','').replace('_','').isalnum():raise RuntimeError('Invalid account history ID')
 directory=Path(c['host'])/'state/cloud-backup-history';directory.mkdir(exist_ok=True)
 key=history_id or ident+'-'+account_id
 if not key.replace('-','').replace('_','').isalnum():raise RuntimeError('Invalid history ID')
 path=directory/(key+'.json')
 previous=json.loads(path.read_text()) if path.exists() else {}
 profile=account.get('profile') or {}
 target={'id':account_id,'name':profile.get('nick_name') or profile.get('name') or profile.get('user_name') or account.get('name') or account_id,'provider':account.get('provider'),'relative_path':account.get('remote_path','GardenZero-Backup')}
 allowed=('state','phase','started','finished','source','snapshot_id','error','last_success','uploaded_bytes','processed_bytes','consistency')
 row={**previous,**{k:v for k,v in (record or {}).items() if k in allowed},'id':path.stem,'type':'cloud','target':target,'job_id':job.get('id'),'pid':job.get('pid',os.getpid()),'process_start':job.get('process_start',Path('/proc/self/stat').read_text().split()[21])}
 if previous.get('started'):row['started']=previous['started']
 row.setdefault('started',job.get('started',datetime.datetime.now(datetime.timezone.utc).isoformat()))
 row.setdefault('state','running');atomic(path,row)
 return path.stem

_EVENT_CACHE={};_EVENT_LOCK=threading.Lock()
_CLOUD_CACHE={};_CLOUD_LOCK=threading.Lock()
def cloud_rows(c):
 directory=Path(c['host'])/'state/cloud-backup-history'
 try:st=directory.stat()
 except FileNotFoundError:return []
 # Writers atomically replace files, changing directory mtime on every update.
 key=(str(directory),st.st_mtime_ns,st.st_ino)
 with _CLOUD_LOCK:
  if _CLOUD_CACHE.get('key')==key:return _CLOUD_CACHE['rows']
  rows=[]
  for path in directory.glob('*.json'):
   try:rows.append(json.loads(path.read_text()))
   except (OSError,ValueError):continue
  _CLOUD_CACHE.update(key=key,rows=rows);return rows
def legacy_cloud_events(c):
 """Recover only recorded per-target events; never invent past capture results."""
 path=Path(c['host'])/'logs/cloud-events.jsonl'
 try:st=path.stat()
 except FileNotFoundError:return []
 key=(str(path),st.st_size,st.st_mtime_ns)
 with _EVENT_LOCK:
  if _EVENT_CACHE.get('key')==key:return _EVENT_CACHE['rows']
  rows=[];pending={}
  with path.open() as stream:
   for index,line in enumerate(stream):
    try:item=json.loads(line)
    except ValueError:continue
    account=item.get('account');operation=item.get('operation')
    if not account:continue
    if operation=='backup-start':
     old=pending.pop(account,None)
     if old and not old.get('skip'):rows.append({**old,'state':'interrupted'})
     pending[account]={'skip':True} if item.get('history_id') else {'id':'legacy-cloud-'+str(index),'type':'cloud','state':'running','started':item.get('time'),'source':item.get('source'),'target':{'id':account,'name':account},'legacy':True}
    elif operation in ('backup-success','backup-failed'):
     row=pending.pop(account,None)
     if row and row.get('skip'):continue
     row=row or {'id':'legacy-cloud-'+str(index),'type':'cloud','target':{'id':account,'name':account},'legacy':True}
     rows.append({**row,'state':'success' if operation=='backup-success' else 'failed','finished':item.get('time'),'snapshot_id':item.get('snapshot_id'),'error':item.get('error')})
  rows.extend({**r,'state':'interrupted'} for r in pending.values() if not r.get('skip'))
  _EVENT_CACHE.update(key=key,rows=rows);return rows

def unified(c,last_disk=None):
 rows=[]
 for index,item in enumerate(history(c,last_disk)):
  if item.get('operation') not in (None,'backup'):continue
  target=item.get('disk') or disk(c)
  rows.append({**{k:item.get(k) for k in ('state','started','time','error','resume_error','last_success')},'id':'disk-'+str(index)+'-'+str(item.get('started') or item.get('time') or ''),'type':'disk','target':{'id':target.get('uid','unknown'),'name':target.get('name','备份磁盘'),'uid':target.get('uid'),'relative_path':target.get('relative_path')}})
 for item in cloud_rows(c):
  row=dict(item)
  if row.get('state')=='running':
   from nas_backup_control import alive
   operation_path=Path(c['host'])/'state/backup-operation.json'
   operation=json.loads(operation_path.read_text()) if operation_path.exists() else {}
   if not alive(row) or (row.get('job_id') and (operation.get('id')!=row['job_id'] or operation.get('state') not in ('running','cancelling'))):row['state']='interrupted'
  rows.append({k:v for k,v in row.items() if k in ('id','type','target','state','started','finished','source','snapshot_id','error','legacy','consistency')})
 names={}
 for filename in ('cloud-accounts.json','cloud-removed-accounts.json'):
  try:accounts=json.loads((Path(c['host'])/'state'/filename).read_text()).get('accounts',[])
  except (OSError,ValueError):continue
  for account in accounts:
   profile=account.get('profile') or {}
   names[account['id']]=profile.get('nick_name') or profile.get('name') or account.get('name') or account['id']
 for item in legacy_cloud_events(c):
  row={**item,'target':dict(item['target'])};row['target']['name']=names.get(row['target']['id'],row['target']['name']);rows.append(row)
 def stamp(row):
  value=row.get('started') or row.get('finished') or row.get('time') or ''
  try:return datetime.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
  except ValueError:return 0
 return sorted(rows,key=stamp,reverse=True)
