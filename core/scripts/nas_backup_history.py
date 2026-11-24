"""Disk backup outcomes, retained independently of an occasionally disconnected target."""
import datetime,json,secrets,os
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
