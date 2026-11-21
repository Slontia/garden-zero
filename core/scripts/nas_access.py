"""Inherited directory ownership, enforced against Compose and observed Docker mounts."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import json,os
from pathlib import Path,PurePosixPath

def policy():
 here=Path(__file__).resolve().parent
 path=here/'data-access.json' if (here/'data-access.json').exists() else here.parent/'templates/data-access.json'
 data=json.loads(path.read_text());seen=set()
 if data.get('schema')!=1:raise RuntimeError('Unsupported data access registry')
 for rule in data['rules']:
  path=rule['path']
  if path in seen or (path!='.' and (str(PurePosixPath(path))!=path or path.startswith('/') or '..' in PurePosixPath(path).parts)):raise RuntimeError('Invalid data access path')
  seen.add(path)
 if '.' not in seen:raise RuntimeError('Missing default data access rule')
 return data

def resolve(data,path):
 path=str(PurePosixPath(path))
 matches=[r for r in data['rules'] if r['path']=='.' or path==r['path'] or path.startswith(r['path']+'/')]
 rule=max(matches,key=lambda r:0 if r['path']=='.' else len(PurePosixPath(r['path']).parts))
 return {**rule,'path':path,'inherited_from':rule['path'],'readers':list(dict.fromkeys(data['readers']+rule['readers']))}

def access_mount(c,source,service,writable,data=None):
 data=data or policy();root=os.path.abspath(c['data']);source=os.path.abspath(source)
 if os.path.commonpath([root,source])!=root:
  # A parent mount would bypass all per-directory ownership.
  if writable and os.path.commonpath([root,source])==source:raise RuntimeError('Writable ancestor of data: '+service)
  return None
 relative=os.path.relpath(source,root).replace(os.sep,'/');rule=resolve(data,relative)
 real_root=os.path.realpath(root);real_source=os.path.realpath(source)
 if Path(source).is_symlink() or os.path.commonpath([real_root,real_source])!=real_root or os.path.relpath(real_source,real_root)!=os.path.relpath(source,root):raise RuntimeError('Linked data mount: '+service)
 if writable:
  if rule['writer']!=service:raise RuntimeError('Data writer mismatch: '+service+' / '+relative)
  for child in data['rules']:
   if (relative=='.' or child['path'].startswith(relative+'/')) and child['writer']!=service:raise RuntimeError('Writable parent crosses ownership: '+relative)
 elif service not in rule['readers'] and service!=rule['writer']:raise RuntimeError('Data reader mismatch: '+service+' / '+relative)
 # Read-only parent access also covers every child, so each child must allow it.
 if not writable:
  for child in data['rules']:
   if (relative=='.' or child['path'].startswith(relative+'/')) and service not in resolve(data,child['path'])['readers'] and service!=child['writer']:raise RuntimeError('Read-only parent crosses ownership: '+relative)
 return {'service':service,'path':relative,'mode':'write' if writable else 'read'}

def validate_compose(c,config):
 data=policy();observed=[]
 for service,definition in config['services'].items():
  for mount in definition.get('volumes',[]):
   if mount.get('type')!='bind':continue
   item=access_mount(c,mount['source'],service,not mount.get('read_only',False),data)
   if item:observed.append(item)
 return observed

def snapshot(c,containers):
 data=policy();paths={r['path'] for r in data['rules']};errors=[];observed=[]
 try:
  paths.update(p.name for p in Path(c['data']).iterdir() if p.is_dir() or p.is_symlink())
 except OSError:errors.append('数据目录当前不可读取')
 for container in containers:
  service=container['Config'].get('Labels',{}).get('com.docker.compose.service','unknown')
  for mount in container.get('Mounts',[]):
   if mount.get('Type')!='bind':continue
   try:
    item=access_mount(c,mount['Source'],service,mount.get('RW',False),data)
    if item:observed.append(item)
   except RuntimeError as error:errors.append(str(error))
 rules=[]
 for path in sorted(paths):
  rule=resolve(data,path);rule['exists']=(Path(c['data'])/path).exists();rule['recursive']=True
  rules.append(rule)
 return {'schema':1,'scope':data['scope'],'inheritance':'最长路径规则优先，所有子目录和新文件继承；未分配目录无应用写入者','rules':rules,'mounts':observed,'state':'mismatch' if errors else 'verified','errors':errors,'maintenance_note':'历史恢复仅在暂停共享且持有生命周期锁时临时生成新副本；主机管理员仍具有独立权限'}

def authorize_restore(c):
 rule=resolve(policy(),'shared')
 if rule['writer']!='syncthing' or rule.get('maintenance',{}).get('writer')!='restore':raise RuntimeError('共享目录未授予历史恢复的临时写入权限')
