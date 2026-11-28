"""NAS-owned full-copy shared space. API keys remain on the host, never in Meta responses."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import json,re,urllib.request,urllib.parse,xml.etree.ElementTree as ET
from pathlib import Path
FOLDER='nas-shared';PATH='/var/syncthing/Shared'

def api(c,path,data=None,method=None):
 key=ET.parse(Path(c['host'])/'state/syncthing/config.xml').findtext('gui/apikey')
 if not key:raise RuntimeError('同步服务认证未配置')
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 req=urllib.request.Request('http://127.0.0.1:8384/rest/'+path,data=json.dumps(data).encode() if data is not None else None,method=method,headers={'X-API-Key':key,'Content-Type':'application/json'})
 with op.open(req,timeout=20) as r:
  body=r.read();return json.loads(body) if body else None

def owned_folder(folder):
 if folder.get('id')!=FOLDER or folder.get('path')!=PATH or folder.get('type')!='sendreceive':raise RuntimeError('共享空间配置不匹配，拒绝覆盖')
 if folder.get('versioning',{}).get('type')!='simple' or folder['versioning'].get('params',{}).get('keep')!='20' or folder.get('maxConflicts')!=-1:raise RuntimeError('共享空间版本/冲突保护配置不匹配')

def prepare(c):
 folders=api(c,'config/folders')
 if any(f.get('id')!=FOLDER for f in folders):raise RuntimeError('存在其他同步空间，请先核实，拒绝自动更改')
 for f in folders:owned_folder(f)
 # No public discovery/relay and no auto acceptance. Only private Serve forwards transport.
 api(c,'config/options',{'listenAddresses':['tcp://0.0.0.0:22000'],'globalAnnounceEnabled':False,'localAnnounceEnabled':False,'relaysEnabled':False,'natEnabled':False},'PATCH')
 if not folders:
  f=api(c,'config/defaults/folder');f.update(id=FOLDER,label='共享',path=PATH,type='sendreceive',ignorePerms=True,maxConflicts=-1,rescanIntervalS=300,fsWatcherEnabled=True,minDiskFree={'value':5,'unit':'%'})
  f['versioning']={'type':'simple','params':{'keep':'20','cleanoutDays':'0'},'cleanupIntervalS':3600,'fsPath':'','fsType':'basic'}
  api(c,'config/folders',f,'POST')
 return summary(c)

def pair(c,device_id,name='Mac'):
 if not isinstance(device_id,str) or len(device_id)>100 or not re.fullmatch(r'[A-Za-z0-9 -]+',device_id):raise RuntimeError('请输入 Syncthing 设备 ID，不能使用 Tailscale ID 或 API Key')
 verified=api(c,'svc/deviceid?'+urllib.parse.urlencode({'id':device_id}))
 if not verified.get('id'):raise RuntimeError('同步设备 ID 无效')
 identity=verified['id'];own=api(c,'system/status')['myID']
 if identity==own:raise RuntimeError('请填写 Mac 的设备 ID，不是 NAS 的 ID')
 folder=api(c,'config/folders/'+FOLDER);owned_folder(folder)
 devices=api(c,'config/devices')
 if not any(d['deviceID']==identity for d in devices):
  d=api(c,'config/defaults/device');d.update(deviceID=identity,name=str(name)[:80] or 'Mac',addresses=['dynamic'],introducer=False,autoAcceptFolders=False)
  api(c,'config/devices',d,'POST')
 if not any(d['deviceID']==identity for d in folder['devices']):
  folder['devices'].append({'deviceID':identity,'introducedBy':'','encryptionPassword':''});api(c,'config/folders/'+FOLDER,folder,'PUT')
 return identity

def unpair(c,device_id):
 if not isinstance(device_id,str) or len(device_id)>100 or not re.fullmatch(r'[A-Za-z0-9 -]+',device_id):raise RuntimeError('同步设备 ID 无效')
 verified=api(c,'svc/deviceid?'+urllib.parse.urlencode({'id':device_id}));identity=verified.get('id')
 if not identity or identity==api(c,'system/status')['myID']:raise RuntimeError('不能解除 NAS 自身的共享')
 folder=api(c,'config/folders/'+FOLDER);owned_folder(folder)
 # Remove only this space's membership. Other devices, identities and data remain intact.
 devices=[d for d in folder['devices'] if d['deviceID']!=identity]
 if devices!=folder['devices']:api(c,'config/folders/'+FOLDER,{'devices':devices},'PATCH')
 return identity

def pause(c,paused):
 if not isinstance(paused,bool):raise RuntimeError('Invalid pause state')
 folder=api(c,'config/folders/'+FOLDER);owned_folder(folder)
 api(c,'config/folders/'+FOLDER,{'paused':paused},'PATCH')

def summary(c):
 f=next((f for f in api(c,'config/folders') if f['id']==FOLDER),None)
 own=api(c,'system/status')['myID']
 if not f:return {'state':'awaiting_folder','device_id':own,'folder_count':0}
 owned_folder(f);remote=[d['deviceID'] for d in f['devices'] if d['deviceID']!=own]
 connections=api(c,'system/connections').get('connections',{})
 names={d['deviceID']:d.get('name') or d['deviceID'][:7] for d in api(c,'config/devices')}
 result={'state':'paused' if f['paused'] else 'awaiting_pairing' if not remote else 'configured','mode':'full_sync','device_id':own,'folder_id':FOLDER,'label':f['label'],'local_path_suggestion':'~/NAS/共享','folder_count':1,'device_count':len(remote)+1,'peers':[{'id':d,'name':names.get(d,d[:7]),'connected':connections.get(d,{}).get('connected',False)} for d in remote],'version_keep':20,'max_conflicts':-1,'paused':f['paused']}
 if not f['paused']:
  db=api(c,'db/status?folder='+FOLDER);result.update(sync_state=db.get('state','unknown'),errors=db.get('pullErrors',0),local_files=db.get('localFiles',0),local_bytes=db.get('localBytes',0),pending_items=db.get('needTotalItems',0),pending_bytes=db.get('needBytes',0),total_bytes=db.get('globalBytes',0),sync_error=db.get('error') or db.get('watchError',''))
  result['completion']=api(c,'db/completion?folder='+FOLDER).get('completion')
 for peer in result['peers']:
  if not peer['connected']:continue
  try:
   completion=api(c,'db/completion?'+urllib.parse.urlencode({'folder':FOLDER,'device':peer['id']}))
   peer.update(completion=completion.get('completion'),pending_items=completion.get('needItems',0)+completion.get('needDeletes',0),pending_bytes=completion.get('needBytes',0),folder_state=completion.get('remoteState','unknown'))
  except Exception:peer['folder_state']='unknown'
 root=Path(c['data'])/'shared';conflicts=[]
 for p in root.rglob('*sync-conflict-*'):
  if p.is_file() and '.stversions' not in p.relative_to(root).parts:conflicts.append(str(p.relative_to(root)))
 result['conflict_count']=len(conflicts);result['conflicts']=conflicts[:100]
 return result

def relative_parts(name):
 if not isinstance(name,str) or not name or len(name)>2000 or '\\' in name or any(ord(c)<32 for c in name):raise RuntimeError('文件路径无效')
 parts=name.split('/')
 if any(p in ('','.','..','.stversions','.stfolder','.stignore') or ':' in p for p in parts):raise RuntimeError('文件路径越界或属于同步元数据')
 return parts

def history(c):
 folder=api(c,'config/folders/'+FOLDER);owned_folder(folder)
 entries=[]
 for name,versions in api(c,'folder/versions?folder='+FOLDER).items():
  try:relative_parts(name)
  except RuntimeError:continue
  for v in versions:entries.append({'path':name,'version':v['versionTime'],'modified':v.get('modTime'),'size':v['size']})
 entries.sort(key=lambda v:(v['version'],v['path']),reverse=True)
 return {'entries':entries[:500],'total':len(entries),'limited':len(entries)>500,'restore_mode':'new_copy'}

def restore_copy(c,name,version):
 """Publish a new copy from an API-listed immutable archive. Never overwrite original/history."""
 import contextlib,datetime,hashlib,os,secrets,shutil,stat
 from nasctl import volume
 parts=relative_parts(name)
 if not isinstance(version,str) or len(version)>80:raise RuntimeError('历史版本标识无效')
 if (Path(c['host'])/'state/deployment-blocked').exists():raise RuntimeError('存储保护中，暂不能恢复')
 volume(c);folder=api(c,'config/folders/'+FOLDER);owned_folder(folder)
 if folder['versioning'].get('fsPath'):raise RuntimeError('自定义历史版本目录尚未验证，拒绝恢复')
 versions=api(c,'folder/versions?folder='+FOLDER)
 record=next((v for v in versions.get(name,[]) if v['versionTime']==version),None)
 if record is None:raise RuntimeError('此历史版本已不存在，请刷新列表')
 size=int(record['size'])
 if size<0 or shutil.disk_usage(c['data']).free-size<c['critical_outer_free']:raise RuntimeError('磁盘余量不足以恢复副本')
 stamp=datetime.datetime.fromisoformat(version.replace('Z','+00:00')).strftime('%Y%m%d-%H%M%S')
 leaf=parts[-1];dot=leaf.rfind('.');tagged=(leaf[:dot]+'~'+stamp+leaf[dot:]) if dot>=0 else leaf+'~'+stamp
 archive_parts=['.stversions',*parts[:-1],tagged]
 fds=[];temp=None;paused_by_us=False;was_paused=folder['paused'];result={}
 def directory(parent,name):return os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
 root=Path(c['data'])/'shared'
 try:
  rootfd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);fds.append(rootfd);owner=os.fstat(rootfd)
  archivefd=directory(rootfd,'.stversions');fds.append(archivefd);fd=archivefd
  if os.fstat(archivefd).st_dev!=os.fstat(rootfd).st_dev:raise RuntimeError('历史目录与共享目录不在同一文件系统，拒绝非原子恢复')
  for part in archive_parts[1:-1]:fd=directory(fd,part);fds.append(fd)
  src=os.open(archive_parts[-1],os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd);fds.append(src);before=os.fstat(src)
  if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size!=size:raise RuntimeError('历史文件类型或大小已变化，请重新核实')
  # Stage inside the ignored version directory on the destination filesystem.
  # Runtime/code may be on another disk; atomic rename must not cross filesystems.
  if not was_paused:pause(c,True);paused_by_us=True
  temp='.restore-copy-'+secrets.token_hex(16)
  tmpfd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=archivefd)
  try:os.fchown(tmpfd,owner.st_uid,owner.st_gid)
  except BaseException:os.close(tmpfd);raise
  digest=hashlib.sha256()
  with os.fdopen(tmpfd,'wb') as out,os.fdopen(os.dup(src),'rb') as source:
   while block:=source.read(1024*1024):out.write(block);digest.update(block)
   out.flush();os.fsync(out.fileno())
  after=os.fstat(src)
  if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise RuntimeError('历史文件在恢复时发生变化，共享保持暂停')
  recovery='Recovered-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(5)
  os.mkdir(recovery,mode=0o750,dir_fd=rootfd);dest=directory(rootfd,recovery);fds.append(dest);os.fchown(dest,owner.st_uid,owner.st_gid)
  for part in parts[:-1]:os.mkdir(part,mode=0o750,dir_fd=dest);dest=directory(dest,part);fds.append(dest);os.fchown(dest,owner.st_uid,owner.st_gid)
  # Unique recovery root is created exclusively; NAS sync is paused and no SMB writer exists.
  os.rename(temp,parts[-1],src_dir_fd=archivefd,dst_dir_fd=dest);temp=None
  result={'restored_path':recovery+'/'+name,'size':size,'sha256':digest.hexdigest(),'sync_resumed':False}
  if not was_paused:
   try:volume(c);pause(c,False);result['sync_resumed']=True
   except Exception:result['warning']='恢复副本已保存，但同步恢复失败；请检查后手动恢复同步'
  return result
 except Exception as e:
  raise RuntimeError('恢复未完成：'+str(e)+('；NAS 共享已暂停，请核实后恢复同步' if paused_by_us else '')) from None
 finally:
  if temp:
   with contextlib.suppress(FileNotFoundError):os.unlink(temp,dir_fd=archivefd)
  for fd in reversed(fds):os.close(fd)
