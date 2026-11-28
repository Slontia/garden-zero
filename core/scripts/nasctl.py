#!/usr/bin/env python3
"""NAS host controller: explicit local Docker endpoint and allowlisted operations."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import argparse,contextlib,datetime,fcntl,hashlib,hmac,http.client,http.server,json,os,shutil,socket,socketserver,subprocess,sys,tarfile,tempfile,threading,time,urllib.request
from pathlib import Path
from nas_policy import backup_due,backup_summary,mirror_due
CONFIG=Path(os.environ.get('NAS_HOST_CONFIG','/mnt/c/nas/system/config/config.json'))
DOCKER=['/usr/bin/docker','--host','unix:///var/run/docker.sock']
MODULES={'cloud':['cloud-gateway'],'identity':['authentik-server','authentik-worker','identity-gateway'],'immich':['immich-server','cache'],'postgres':['database'],'ml':['immich-machine-learning'],'meta':['meta'],'knowledge':['joplin'],'git':['git'],'smb':['smb'],'syncthing':['syncthing'],'tailscale':['tailscale']}
WRITER=None
LOCK_ROOT=Path('/run/nas')
_APP_OPERATIONS={};_APP_OPERATIONS_LOCK=threading.Lock()
BUSINESS=['immich','knowledge','git','smb','syncthing','ml']
def cfg():
 c=json.loads(CONFIG.read_text())
 source=Path(__file__).resolve()
 if source.parent.name=='scripts':
  checkout=source.parents[2]
  if Path(c.get('core',str(Path(c['root'])/'core'))).resolve()!=source.parent.parent or Path(c['root']).resolve()!=checkout:
   raise RuntimeError('Checkout does not match configured NAS root; refusing to control another instance')
 return c
def run(args,**kw):
 return subprocess.run([str(x) for x in args],check=True,text=True,**kw)
def output(args):return subprocess.check_output([str(x) for x in args],text=True).strip()
def compose(c,*args,**kw):
 return run(DOCKER+['compose','--project-name',c['project'],'--env-file',str(Path(c['host'])/'config/compose.env'),'-f',str(Path(c['host'])/'config/nas.yml')]+list(args),**kw)
def audit(c,action,detail):
 p=Path(c['host'])/'logs/operations.jsonl'
 with p.open('a') as f:
  fcntl.flock(f,fcntl.LOCK_EX)
  f.write(json.dumps({'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'action':action,'detail':detail},ensure_ascii=False)+'\n')
@contextlib.contextmanager
def lock(shared=False,blocking=False):
 LOCK_ROOT.mkdir(mode=0o750,exist_ok=True)
 with open(LOCK_ROOT/'lifecycle.lock','a') as stream:
  try:fcntl.flock(stream,(fcntl.LOCK_SH if shared else fcntl.LOCK_EX)|(0 if blocking else fcntl.LOCK_NB))
  except BlockingIOError:raise RuntimeError('有应用或互斥维护操作正在进行，请稍后重试')
  yield

@contextlib.contextmanager
def application_lock(app,operation):
 from nas_apps import APPS,canonical_app
 app=canonical_app(app)
 if app not in APPS or APPS[app].get('protected'):raise RuntimeError('Invalid or protected application')
 with lock(shared=True):
  with open(LOCK_ROOT/('app-'+app+'.lock'),'a') as stream:
   try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:raise RuntimeError('此应用正在更新，请等待本次操作完成')
   with _APP_OPERATIONS_LOCK:_APP_OPERATIONS[app]={**operation,'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
   try:yield
   finally:
    with _APP_OPERATIONS_LOCK:_APP_OPERATIONS.pop(app,None)

def application_operations():
 with _APP_OPERATIONS_LOCK:return {key:dict(value) for key,value in _APP_OPERATIONS.items()}

@contextlib.contextmanager
def database_lock():
 LOCK_ROOT.mkdir(mode=0o750,exist_ok=True)
 with open(LOCK_ROOT/'postgres.lock','a') as stream:
  fcntl.flock(stream,fcntl.LOCK_EX)
  yield

def volume(c):
 if not Path(c['root']).is_dir() or not Path(c['data']).is_dir():raise RuntimeError('NAS SSD/data directory missing')
 from nas_storage import configured,verify
 if configured(c):verify(c)
 elif 'microsoft' in output(['uname','-r']).lower():
  # Stable volume identity, never infer identity from D: existing alone.
  ps=shutil.which('powershell.exe') or '/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe'
  parts=Path(c['root']).parts
  drive=c.get('volume_drive',parts[2] if len(parts)>2 and parts[1]=='mnt' else '')
  if len(drive)!=1 or not drive.isascii() or not drive.isalpha() or parts[:3]!=('/', 'mnt', drive.lower()):raise RuntimeError('Unsupported Windows NAS root/drive')
  code="[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-Volume -DriveLetter '"+drive+"' | Select-Object UniqueId,FileSystem | ConvertTo-Json -Compress"
  v=json.loads(output([ps,'-NoProfile','-NonInteractive','-Command',code]))
  if c['volume_guid'].lower()!=v['UniqueId'].split('{')[-1].split('}')[0].lower() or v['FileSystem']!='NTFS':raise RuntimeError('Wrong NAS volume identity/filesystem')
 else:
  expected=c.get('linux_volume_uuid')
  if not expected:raise RuntimeError('Linux volume identity adapter not configured')
  if output(['findmnt','-n','-o','UUID','--target',c['root']])!=expected:raise RuntimeError('Linux volume UUID mismatch')
 if shutil.disk_usage(c['data']).free<c['critical_outer_free']:raise RuntimeError('Outer disk free space below critical threshold')
 if shutil.disk_usage(Path(c['image']).parent).free<c.get('critical_image_outer_free',c['critical_outer_free']):raise RuntimeError('Database backing disk free space below critical threshold')

def mounted(c):
 p=Path(c['mount'])
 return p.is_dir() and subprocess.run(['mountpoint','-q',str(p)]).returncode==0

def verify_mount(c):
 if not mounted(c):raise RuntimeError('Database mount missing; refusing empty-directory fallback')
 info=json.loads(output(['findmnt','-J','-o','SOURCE,FSTYPE,UUID','--target',c['mount']]))['filesystems'][0]
 if info['fstype']!='ext4' or info['uuid']!=c['image_uuid']:raise RuntimeError('Database filesystem/UUID mismatch')
 source=info['source']
 backing=output(['losetup','-n','-O','BACK-FILE',source])
 if Path(backing).resolve()!=Path(c['image']).resolve():raise RuntimeError('Database loop backing file mismatch')
 return source

def mount(c):
 from nas_storage import configured,mount_data
 if configured(c):mount_data(c)
 if (Path(c['host'])/'state/deployment-blocked').exists():raise RuntimeError('Storage incident unresolved; mount is blocked')
 volume(c)
 image=Path(c['image'])
 if image.is_symlink() or not image.is_file():raise RuntimeError('Database image missing or symlink')
 if output(['blkid','-s','UUID','-o','value',image])!=c['image_uuid']:raise RuntimeError('Image UUID mismatch')
 if mounted(c):verify_mount(c);return
 if output(['losetup','-j',image]):raise RuntimeError('Image already attached outside managed mount; inspect before continuing')
 # Read-only check, never run a repairing fsck automatically.
 run(['e2fsck','-fn',image],stdout=subprocess.DEVNULL)
 point=Path(c['mount']);point.mkdir(parents=True,exist_ok=True)
 if any(point.iterdir()):raise RuntimeError('Nonempty unmounted database directory; refusing mount')
 loop=output(['losetup','--find','--show','--nooverlap',image])
 try:
  run(['mount','-t','ext4','-o','nodev,nosuid,noexec',loop,point]);verify_mount(c)
 except Exception:
  if not mounted(c):run(['losetup','-d',loop])
  raise
 (point/'postgres').mkdir(mode=0o700,exist_ok=True)
 audit(c,'mount',{'uuid':c['image_uuid']})
 from nas_recovery import record_storage_validation
 record_storage_validation(c)

def inventory(c):
 ids=output(DOCKER+['ps','-aq','--filter','label=com.docker.compose.project='+c['project']]).split()
 if not ids:return []
 rows=json.loads(output(DOCKER+['inspect']+ids))
 return [{'name':r['Name'].lstrip('/'),'service':r['Config']['Labels'].get('com.docker.compose.service'),'state':r['State']['Status'],'health':r['State'].get('Health',{}).get('Status','unknown'),'exit_code':r['State'].get('ExitCode'),'oom':r['State'].get('OOMKilled',False)} for r in rows]

def wait_services(c,services,timeout=180):
 deadline=time.monotonic()+timeout
 while time.monotonic()<deadline:
  inv={x['service']:x for x in inventory(c)}
  failed=[s for s in services if s in inv and inv[s]['state'] in ('exited','dead')]
  if failed:raise RuntimeError('Services exited: '+','.join(failed))
  if all(s in inv and inv[s]['state']=='running' and inv[s]['health']=='healthy' for s in services):return
  time.sleep(2)
 raise RuntimeError('Application readiness timeout: '+','.join(services))

def desired_state(c):
 path=Path(c['host'])/'state/desired-modules.json'
 try:state=json.loads(path.read_text()) if path.exists() else None
 except (OSError,ValueError) as error:raise RuntimeError('Cannot read persisted service selection') from error
 if state is None:
  legacy=read_state(c,'autostart.json',c['autostart']);disabled=read_state(c,'disabled.json',[])
  modules=[m for m in legacy if m not in disabled]
  state={'schema':1,'modules':modules,'ml_preference':'ml' in modules}
 if isinstance(state,dict) and isinstance(state.get('modules'),list):state={**state,'modules':[m for m in state['modules'] if m!='files']}
 if state.get('schema')!=1 or not isinstance(state.get('modules'),list) or any(m not in MODULES for m in state['modules']):raise RuntimeError('Invalid persisted service selection')
 state={**state,'modules':list(dict.fromkeys(state['modules']+['meta','tailscale','postgres']+(['identity'] if c.get('sso',{}).get('prepared') else [])+(['cloud'] if c.get('cloud_bridge_prepared') else [])))}
 if 'immich' not in state['modules']:state['modules']=[m for m in state['modules'] if m!='ml']
 return state

def remember(c,modules,enabled):
 with open(Path(c['host'])/'state/.desired-modules.lock','a') as stream:
  fcntl.flock(stream,fcntl.LOCK_EX)
  _remember(c,modules,enabled)

def _remember(c,modules,enabled):
 state=desired_state(c);selection=set(state['modules'])
 selection.update(modules) if enabled else selection.difference_update(modules)
 if 'ml' in modules and ('immich' not in modules or enabled):state['ml_preference']=enabled
 state['modules']=[m for m in MODULES if m in selection or m in ('meta','tailscale','postgres')]
 path=Path(c['host'])/'state/desired-modules.json'
 fd,tmp=tempfile.mkstemp(prefix='.desired-',dir=path.parent)
 try:
  with os.fdopen(fd,'w') as stream:
   json.dump(state,stream);stream.flush();os.fsync(stream.fileno())
  os.replace(tmp,path)
 finally:
  if Path(tmp).exists():Path(tmp).unlink()

def autostart(c):
 # Compatibility for host scripts; browser authentication never calls this.
 return desired_state(c)['modules']


def start(c,module,enable_all=False):
 if (Path(c['host'])/'state/deployment-blocked').exists():raise RuntimeError('Storage incident unresolved; NAS write startup is blocked. See C:\\nas\\system\\state\\storage-incident.json')
 from nas_storage import configured,mount_data
 if configured(c):mount_data(c)
 if module=='all':
  modules=list(MODULES) if enable_all else autostart(c)
  if not c.get('cloud_bridge_prepared'):modules=[m for m in modules if m!='cloud']
  if not c['ml_enabled']:modules=[m for m in modules if m!='ml']
  if not c.get('sso',{}).get('prepared'):modules=[m for m in modules if m!='identity']
 elif module=='immich':modules=['immich']+(['ml'] if c['ml_enabled'] and desired_state(c).get('ml_preference',True) else [])
 elif module=='ml':
  active={r['service']:r for r in inventory(c)}
  if not all(active.get(s,{}).get('state')=='running' and active[s]['health']=='healthy' for s in MODULES['immich']):raise RuntimeError('请先开启相册，再启用智能检索')
  modules=['ml']
 elif module in MODULES:modules=[module]
 else:raise RuntimeError('Unknown module')
 if c.get('sso',{}).get('enabled') and any(m in ('meta','files','immich','knowledge') for m in modules) and 'identity' not in modules:
  active={row['service']:row for row in inventory(c)}
  if not all(active.get(service,{}).get('health')=='healthy' and active[service]['state']=='running' for service in MODULES['identity']):modules=['identity']+modules
 if 'identity' in modules and not (Path(c['host'])/'config/authentik.env').is_file():raise RuntimeError('Prepared identity credentials are missing; refusing default identity startup')
 from nas_access import validate_compose
 definition=json.loads(output(DOCKER+['compose','--project-name',c['project'],'--env-file',str(Path(c['host'])/'config/compose.env'),'-f',str(Path(c['host'])/'config/nas.yml'),'--profile','*','config','--format','json']))
 validate_compose(c,definition)
 if 'ml' in modules and not c['ml_enabled']:raise RuntimeError('Machine learning pending memory decision')
 if 'ml' in modules:
  total=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:')))*1024
  if total<5*1024**3:raise RuntimeError('WSL memory increase not active; restart WSL before starting machine learning')
 remember(c,modules,True)
 infra=[m for m in modules if m in ('meta','tailscale')]
 if 'meta' in modules:run(['systemctl','start','nas-agent.service'],stdout=subprocess.DEVNULL)
 if infra:
  compose(c,'up','-d','--no-build',*sum([MODULES[m] for m in infra],[]))
 if 'identity' in modules:
  with database_lock():
   from nas_sso import renew_certificate
   gateway=next((row for row in inventory(c) if row['service']=='identity-gateway'),{})
   renew_certificate(c,reload=gateway.get('state')=='running')
 if any(m in ('immich','ml','knowledge','postgres','identity') for m in modules):
  mount(c)
  with database_lock():
   compose(c,'up','-d','--no-build','--wait','--wait-timeout','120','database')
   wait_services(c,['database'])
   from nas_database import provision
   provision(c)
 if any(m in BUSINESS for m in modules):volume(c)
 services=sum([MODULES[m] for m in modules],[])
 if services:compose(c,'up','-d','--no-build','--wait','--wait-timeout','240',*services)
 if services:wait_services(c,services)
 if 'knowledge' in modules:
  from nas_joplin import prepare
  prepare(c)
 if 'meta' in modules:
  from nas_ready import wait_meta_ready
  wait_meta_ready(c,modules)
 from nas_recovery import manual_started
 manual_started(c,services)
 audit(c,'start',modules)

def stop(c,module,remember_selection=True):
 if module=='all':
  from nas_recovery import pause
  pause(c)
 modules=BUSINESS if module=='business' else list(MODULES) if module=='all' else ['immich','ml'] if module=='immich' else [module]
 if any(m not in MODULES for m in modules):raise RuntimeError('Unknown module')
 if remember_selection:remember(c,modules,False)
 if any(m in modules for m in ('smb','tailscale')):
  if WRITER:WRITER.revoke('module-stop')
  else:
   from nas_writer import Writer
   Writer(c,audit).revoke('cli-module-stop')
 services=sum([MODULES[m] for m in modules],[])
 # Application first; database last. `stop` never removes volumes or images.
 application=[s for s in services if s not in ('database','cache','meta','tailscale')]
 existing={x['service'] for x in inventory(c)}
 for group in [application,['cache'],['database'],['meta'],['tailscale']]:
  selected=[s for s in group if s in services and s in existing]
  if selected:compose(c,'stop','--timeout','90',*selected)
 for x in inventory(c):
  if x['service'] in services and (x['state']!='exited' or x['exit_code']==137 or (x['service']=='database' and x['exit_code']!=0)):
   raise RuntimeError('Unclean stop; database image retained mounted: '+x['service'])
 if module=='all':
  unmount(c)
  from nas_storage import configured,unmount_data
  if configured(c):unmount_data(c)
 audit(c,'stop',modules)

def unmount(c):
 if any(x['service'] in ('database','immich-server','immich-machine-learning','joplin','authentik-server','authentik-worker') and x['state']=='running' for x in inventory(c)):raise RuntimeError('Database consumers still running')
 for row in inventory(c):
  if row['service']=='database' and row['exit_code']!=0:raise RuntimeError('Unclean database exit; refusing unmount')
 if mounted(c):
  loop=verify_mount(c);run(['umount',c['mount']]);run(['losetup','-d',loop]);audit(c,'unmount','success')
 elif output(['losetup','-j',c['image']]):raise RuntimeError('Unmanaged loop attachment remains')

def status(c):
 result={'project':c['project'],'modules':{},'disk':{},'desired_modules':autostart(c),'autostart':autostart(c)}
 try:
  rows=inventory(c);result['docker']='ready'
  for m,services in MODULES.items():
   selected=[x for x in rows if x['service'] in services]
   state='stopped' if not selected or all(x['state']=='exited' for x in selected) else 'ready' if len(selected)==len(services) and all(x['state']=='running' and x['health']=='healthy' for x in selected) else 'starting' if all(x['state']=='running' for x in selected) else 'fault'
   if m=='ml' and not c['ml_enabled']:state='pending_memory'
   result['modules'][m]={'state':state,'containers':selected,'services':services}
 except Exception:result['docker']='unavailable'
 try:
  ids=output(DOCKER+['ps','-aq','--filter','label=com.docker.compose.project='+c['project']]).split()
  inspected=json.loads(output(DOCKER+['inspect']+ids)) if ids else []
  from nas_access import snapshot
  result['data_access']=snapshot(c,inspected)
 except Exception as error:result['data_access']={'state':'unavailable','rules':[],'mounts':[],'errors':[str(error)]}

 for name,key in [('ssd','data'),('backup','backup_root'),('database','mount')]:
  try:
   if name=='database':verify_mount(c)
   if name=='ssd' and c.get('data_storage',{}).get('driver')=='btrfs':
    from nas_storage import verify
    verify(c)
   d=shutil.disk_usage(c[key]);result['disk'][name]={'total':d.total,'free':d.free,'used':d.used}
  except Exception:result['disk'][name]={'state':'unavailable'}
 try:
  p=Path(c['image']);st=p.stat();result['disk']['image']={'logical':st.st_size,'allocated':st.st_blocks*512,'location':'internal' if str(p).startswith('/mnt/c/nas/') else 'ssd','backing_free':shutil.disk_usage(p.parent).free}
 except OSError:pass
 from nas_resources import snapshot as resource_snapshot
 result['resources']=resource_snapshot(c['project'],inspected if 'inspected' in locals() else None)
 result['recovery']=read_state(c,'container-recovery.json',{'services':{}})
 result['sso']={'enabled':bool(c.get('sso',{}).get('enabled'))}
 result['joplin']=read_state(c,'joplin-account.json',{'state':'not_configured'})
 result['operations']=application_operations()
 result['storage_blocked']=(Path(c['host'])/'state/deployment-blocked').exists()
 result['backup']=backup_summary(c,read_state(c,'backup.json',{'state':'not_run'}),datetime.datetime.now(),read_state(c,'schedules.json',{'backup':True}).get('backup',True))
 try:result['backup']['same_disk_as_database']=os.stat(Path(c['image']).parent).st_dev==os.stat(c['backup_root']).st_dev
 except OSError:result['backup']['same_disk_as_database']=None
 result['backup']['external_mirror_configured']=bool(c.get('hdd_root'))
 result['observation']=read_state(c,'soak.json',{'state':'not_run'})
 result['schedules']=read_state(c,'schedules.json',{'backup':True,'cloud':True})
 from nas_cloud import summary
 accounts=summary(c)
 from nas_backup_control import status as backup_control_status
 result['backup_control']=backup_control_status(c)
 result['cloud']={'state':('configured' if all(a['state']=='ready' for a in accounts) else 'login_required') if accounts else 'configured' if c.get('cloud_repository') else 'not_configured','accounts':accounts,'prepared':bool(c.get('cloud_bridge_prepared')),'last':read_state(c,'cloud-backup.json',{}),'continuous':read_state(c,'cloud-snapshot.json',{}),'online':read_state(c,'cloud-online.json',{})}
 hdd_last=read_state(c,'hdd-backup.json',{})
 from nas_backup_history import history,disk,unified
 result['backup_history']=unified(c,hdd_last)
 result['hdd']={'disk':disk(c),'history':history(c,hdd_last),'state':hdd_last.get('state','configured') if c.get('hdd_root') else 'not_connected','mode':c.get('hdd_mode'),'path':c.get('hdd_root'),'last':hdd_last}
 try:
  result['git']={'state':'awaiting_public_key' if not (Path(c['host'])/'state/git/authorized_keys').read_text().strip() else 'configured','repositories':[p.name for p in (Path(c['data'])/'git').glob('*.git') if p.is_dir() and not p.is_symlink()]}
 except OSError:result['git']={'state':'storage_unavailable','repositories':[]}
 result['syncthing']={'state':'unavailable'}
 try:
  import xml.etree.ElementTree as ET
  syn=ET.parse(Path(c['host'])/'state/syncthing/config.xml').getroot()
  folders=syn.findall('folder');devices=syn.findall('device')
  result['syncthing']={'state':'awaiting_folder' if not folders else 'awaiting_pairing' if len(devices)<2 else 'configured','folder_count':len(folders),'device_count':len(devices)}
  if result['modules'].get('syncthing',{}).get('state')=='ready' and result['syncthing']['state']!='configured':result['modules']['syncthing']['state']='pairing_required'
 except (OSError,ET.ParseError):pass
 if c.get('file_access',{}).get('mode')=='full_sync':
  try:
   from nas_sync import summary
   result['syncthing']=summary(c)
  except Exception as e:result['syncthing']={'state':'unavailable','error':str(e)}
 result['file_access']=writer_snapshot(c)
 try:
  from nas_writer import peers
  result['file_access']['devices']=peers()
 except Exception:result['file_access']['devices']=[]
 result['remote']={'state':'login_required'}
 try:
  ts=json.loads(output(DOCKER+['exec','nas-tailscale-1','tailscale','status','--json']))
  result['remote']={'state':ts.get('BackendState','unknown'),'dns':ts.get('Self',{}).get('DNSName','')}
 except Exception:pass
 if result['remote']['state']!='Running' and result['modules'].get('tailscale',{}).get('state')=='ready':result['modules']['tailscale']['state']='login_required'
 from nas_apps import snapshot as application_snapshot
 result['apps']=application_snapshot(c,result['modules'],desired_state(c))
 return result

def writer_snapshot(c):
 if WRITER:return WRITER.snapshot()
 class Connection(http.client.HTTPConnection):
  def connect(self):
   self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(5);self.sock.connect('/run/nas-control/control.sock')
 conn=Connection('localhost')
 try:
  token=(Path(c['host'])/'secrets/agent-token').read_text().strip()
  conn.request('GET','/writer',headers={'Authorization':'Bearer '+token})
  response=conn.getresponse()
  if response.status!=200:raise RuntimeError('Writer status unavailable')
  return json.loads(response.read())
 except Exception:return {'mode':'unknown','active_writer':None,'writer_selection':'unavailable'}
 finally:conn.close()

def read_state(c,name,default):
 p=Path(c['host'])/'state'/name
 try:return json.loads(p.read_text())
 except (OSError,ValueError):return default

def db_signature(container,database=None):
 if database is None:
  from nas_database import primary_database
  database=primary_database()
 names=output(DOCKER+['exec',container,'psql','-U','postgres','-d',database,'-Atc',"SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"]).splitlines()
 if not names:return {}
 chunks=[]
 for name in names:
  ident='"'+name.replace('"','""')+'"';literal="'"+name.replace("'","''")+"'"
  chunks.append('SELECT '+literal+" || ':' || count(*)::text FROM public."+ident)
 text=output(DOCKER+['exec',container,'psql','-U','postgres','-d',database,'-Atc',' UNION ALL '.join(chunks)])
 return dict(line.rsplit(':',1) for line in text.splitlines())

def data_tree(root):
 tree={}
 for directory,dirs,files in os.walk(root,followlinks=False):
  for name in sorted(dirs+files):
   p=Path(directory)/name;st=p.lstat()
   tree[str(p.relative_to(root))]=(st.st_mode,st.st_size,st.st_mtime_ns,os.readlink(p) if p.is_symlink() else None)
 return tree

def backup(c,*,incremental=False):
 from nas_backup_control import operation
 with operation(c,'capture'):
  return _capture_backup(c,incremental=incremental)

def _capture_backup(c,*,incremental=False):
 from nas_backup_control import check,ACTIVE,Interrupted,run as backup_run
 control=ACTIVE.get()
 check()
 if WRITER:WRITER.revoke('backup')
 else:
  from nas_writer import Writer
  Writer(c,audit).revoke('cli-backup')
 last=read_state(c,'backup.json',{})
 inv={x['service']:x for x in inventory(c)}
 if inv.get('database',{}).get('state')!='running':
  record={'state':'skipped','reason':'Database module stopped','last_success':last.get('last_success'),'path':last.get('path')}
  (Path(c['host'])/'state/backup.json').write_text(json.dumps(record));audit(c,'backup-skip',record['reason']);return record
 volume(c);verify_mount(c)
 root=Path(c['backup_root'])
 if incremental:
  from nas_continuous import roots,Builder,fingerprint
  root=roots(c)
 estimated=0 if incremental else sum(p.stat().st_size for p in Path(c['data']).rglob('*') if p.is_file() and not p.is_symlink())
 if not incremental and shutil.disk_usage(root).free < estimated*1.1+5*1024**3:raise RuntimeError('Insufficient internal backup space for this content set')
 if not root.is_dir() or root.is_symlink() or str(root).startswith('/mnt/d/'):raise RuntimeError('Backup root must be the configured real internal disk directory')
 if shutil.disk_usage(root).free<5*1024**3:raise RuntimeError('Less than 5 GiB backup space free')
 reserve=None
 if incremental:
  database_bytes=int(output(DOCKER+['exec',c['project']+'-database-1','psql','-U','postgres','-d','postgres','-Atc','SELECT COALESCE(SUM(pg_database_size(oid)),0) FROM pg_database WHERE NOT datistemplate']))
  reserve=max(1024**3,c.get('critical_image_outer_free',c.get('critical_outer_free',0)))+max(1024**3,database_bytes*2+256*1024**2)
  if shutil.disk_usage(root).free<reserve:raise RuntimeError('Insufficient snapshot space above configured database reserve')
 stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');stage=root/('.incomplete-'+stamp);stage.mkdir()
 statefile='cloud-snapshot.json' if incremental else 'backup.json'
 last=read_state(c,statefile,{})
 builder=Builder(c,stage,reserve=reserve) if incremental else None
 record={**last,'state':'running','started':stamp,'phase':'precopy' if incremental else 'snapshot'};statepath=Path(c['host'])/'state'/statefile
 statepath.write_text(json.dumps(record))
 paused=[]
 try:
  if builder:
   builder.prepare()
   if shutil.disk_usage(root).free<reserve:raise RuntimeError('Snapshot disk reserve unavailable; writers remain running')
  record['phase']='consistent_capture';statepath.write_text(json.dumps(record))
  inv={x['service']:x for x in inventory(c)}
  if inv.get('database',{}).get('state')!='running':raise RuntimeError('Database stopped: backup skipped, no fresh success')
  from nas_apps import backup_services
  active=[service for service in backup_services(MODULES) if inv.get(service,{}).get('state')=='running']
  control.pause()
  if active:
   paused+=active
   compose(c,'stop','--timeout','90',*active)
   stopped={x['service']:x for x in inventory(c)}
   if any(stopped[service]['state']!='exited' or stopped[service]['exit_code']==137 for service in active):raise RuntimeError('Backup writer did not stop cleanly')
  check()
  from nas_database import application_databases,primary_database
  primary=primary_database();databases={}
  for database in application_databases(c):
   check()
   if not database.replace('_','').isalnum():raise RuntimeError('Invalid registered database name')
   filename='database.dump' if database==primary else database+'.dump'
   with (stage/filename).open('wb') as f:
    backup_run(DOCKER+['exec',c['project']+'-database-1','pg_dump','-U','postgres','-d',database,'-Fc'],stdout=f,check=True)
   databases[database]={'dump':filename,'tables':db_signature(c['project']+'-database-1',database)}
  if len(databases)>1:
   with (stage/'database-roles.sql').open('wb') as f:
    backup_run(DOCKER+['exec',c['project']+'-database-1','pg_dumpall','-U','postgres','--roles-only','--no-role-passwords'],stdout=f,check=True)
  # Stop business writers before snapshotting application-managed media and repositories.
  before=None if builder else data_tree(c['data'])
  if builder:builder.prepare(final=True)
  else:
   with tarfile.open(stage/'user-content.tar','w') as tar:
    def checked_member(member):check();return member
    tar.add(c['data'],arcname='data',recursive=True,filter=checked_member)
  if not builder and before!=data_tree(c['data']):raise RuntimeError('User content changed during backup; refusing a COMPLETE snapshot')
  from nas_backup_config import archive_config
  archive_config(c,stage/'configuration.tar')
  manifest={'schema':3,'databases':databases,'layout':'data-core-system','project':c['project'],'scope':'database-user-content-configuration-deploy','time':stamp,'database':primary,'image_uuid':c['image_uuid'],'tables':db_signature('nas-database-1'),'files':{}}
  if builder:manifest.update(schema=4,content=builder.entries,transport_layout='directory-v1',captured_at=time.time(),fingerprint=fingerprint(c,tree=builder.final_rows))
  check()
  for p in stage.iterdir():
   check()
   if builder and p.name=='data':continue
   h=hashlib.sha256()
   with p.open('rb') as f:
    for block in iter(lambda:f.read(1024**2),b''):check();h.update(block)
   manifest['files'][p.name]=h.hexdigest()
  (stage/'manifest.json').write_text(json.dumps(manifest,indent=2))
  if builder:
   from nas_mirror import durable_tree,sync_directory
   for payload in stage.iterdir():
    if payload.is_file():
     with payload.open('rb') as stream:os.fsync(stream.fileno())
   durable_tree(stage)
  check()
  with (stage/'COMPLETE').open('w') as marker:
   marker.write(stamp+'\n');marker.flush();os.fsync(marker.fileno())
  if builder:sync_directory(stage)
  dest=root/stamp;stage.rename(dest)
  if builder:sync_directory(root)
  record={'state':'success','last_success':stamp,'path':str(dest)}
  if builder:record.update(captured_at=manifest['captured_at'],fingerprint=manifest['fingerprint'],copied_bytes=builder.copied,reused_files=builder.reused,phase='ready')
  audit(c,'cloud-snapshot' if incremental else 'backup',str(dest))
 except Exception as e:
  record={**last,'state':'interrupted' if isinstance(e,Interrupted) else 'failed','error':str(e),'last_success':last.get('last_success'),'phase':'failed'};raise
 finally:
  control.resume()
  try:statepath.write_text(json.dumps(record))
  finally:
   if paused:
    try:compose(c,'start','--wait','--wait-timeout','240',*paused)
    except Exception as error:
     record['resume_error']=str(error)
     if record['state']=='success':record['state']='success_with_resume_error'
     statepath.write_text(json.dumps(record));audit(c,'backup-resume-error',str(error));raise
   control.resumed()
 return record

def backup_with_mirror(c,purge_history=False):
 from nas_backup_control import operation
 with operation(c,'disk'):
  return _backup_with_mirror(c,purge_history)

def _backup_with_mirror(c,purge_history=False):
 try:result=backup(c)
 except (Exception,KeyboardInterrupt) as error:
  if c.get('hdd_root'):
   from nas_backup_history import preparation_failure
   preparation_failure(c,error)
  raise
 if result['state']!='success' and c.get('hdd_root'):
  from nas_backup_history import preparation_failure
  preparation_failure(c,RuntimeError('数据准备未完成，磁盘备份未执行'))
 if result['state']=='success' and c.get('hdd_root'):
  from nas_backup_control import run as backup_run
  backup_run(['/usr/bin/python3',str(Path(c['host'])/'installed/external-backup.py'),'hdd','backup']+(['--purge-history'] if purge_history else []),check=True)
 return result

def logs(c,module):
 if module not in MODULES:raise RuntimeError('Unknown module')
 return subprocess.check_output(DOCKER+['compose','--project-name',c['project'],'--env-file',str(Path(c['host'])/'config/compose.env'),'-f',str(Path(c['host'])/'config/nas.yml'),'logs','--no-color','--tail','80']+MODULES[module],text=True,stderr=subprocess.STDOUT)[-24000:]

def action(c,data):
 operation=data.get('action');module=data.get('module')
 if operation=='writer':
  with lock():
   if WRITER is None:raise RuntimeError('写入网关不可用')
   identity=data.get('device')
   if identity is None:WRITER.revoke('administrator')
   elif isinstance(identity,str) and len(identity)<=128:WRITER.grant(identity)
   else:raise RuntimeError('Invalid device identity')
 elif operation in ('sync-pair','sync-pause','sync-unpair','sync-restore-copy'):
  if c.get('file_access',{}).get('mode')!='full_sync':raise RuntimeError('同步空间未配置')
  with lock():
   from nas_sync import pair,pause,unpair,restore_copy
   if operation=='sync-pair':identity=pair(c,data.get('device'),data.get('name','Mac'));audit(c,'sync-pair',{'device':identity})
   elif operation=='sync-unpair':identity=unpair(c,data.get('device'));audit(c,'sync-unpair',{'device':identity})
   elif operation=='sync-restore-copy':
    from nas_access import authorize_restore
    authorize_restore(c)
    result=restore_copy(c,data.get('path'),data.get('version'));audit(c,'sync-restore-copy',result);return result
   else:pause(c,data.get('paused'));audit(c,'sync-pause',{'paused':data.get('paused')})
 elif operation in ('app-power','app-feature'):
  from nas_apps import power,feature,APPS,canonical_app
  app=canonical_app(data.get('app'));enabled=data.get('enabled');name=data.get('feature')
  if app not in APPS or not isinstance(enabled,bool):raise RuntimeError('Invalid application operation')
  if operation=='app-feature' and not any(item['id']==name for item in APPS[app].get('features',[])):raise RuntimeError('Unknown application feature')
  with application_lock(app,{'action':operation,'enabled':enabled,'feature':name if operation=='app-feature' else None}):
   if operation=='app-power':power(c,app,enabled)
   else:feature(c,app,name,enabled)
 elif operation=='backup-settings':
  raise RuntimeError('在线备份不再配置停写时间')
 elif operation=='backup-cancel':
  from nas_backup_control import cancel,status as backup_status
  if backup_status(c)['operation'].get('kind')!='cloud':raise RuntimeError('网页仅可中断网盘备份任务')
  return cancel(c,data.get('job'))
 elif operation in ('cloud-auto','cloud-delete'):
  from nas_cloud import action as cloud_action
  return cloud_action(c,data)
 elif operation in ('cloud-authorize','cloud-authorize-poll','cloud-authorize-cancel','cloud-verify','cloud-delete','cloud-backup'):
  from nas_cloud import action as cloud_action
  with lock(shared=operation in ('cloud-authorize','cloud-authorize-poll','cloud-authorize-cancel')):return cloud_action(c,data)
 elif operation=='enable-all':
  with lock():start(c,'all',enable_all=True)
 elif operation in ('start','stop','restart'):
  if module not in MODULES and module not in ('all','business'):raise RuntimeError('Invalid module')
  if operation in ('stop','restart') and module in ('cloud','meta','tailscale','postgres','identity','all'):raise RuntimeError('管理与远程连接由主机维护，不能在网页中关闭')
  if operation=='restart' and module in ('cloud','meta','tailscale','postgres','identity','all','business'):raise RuntimeError('Restart this infrastructure module through the host CLI')
  with lock():
   if operation in ('stop','restart'):stop(c,module)
   if operation in ('start','restart'):start(c,module)
 elif operation in ('backup','hdd-backup'):
  raise RuntimeError('磁盘备份仅可通过宿主脚本执行')
 elif operation=='schedule':
  task=data.get('task');enabled=data.get('enabled')
  if task not in ('backup','cloud') or not isinstance(enabled,bool):raise RuntimeError('Invalid schedule')
  with lock():
   schedules=read_state(c,'schedules.json',{'backup':True,'cloud':True});schedules[task]=enabled
   (Path(c['host'])/'state/schedules.json').write_text(json.dumps(schedules))
 else:raise RuntimeError('Operation not allowed')

class Agent(http.server.BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def reply(self,code,data):
  b=json.dumps(data,ensure_ascii=False).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def authorized(self):
  c=cfg();token=(Path(c['host'])/'secrets/agent-token').read_text().strip()
  return hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token)
 def do_GET(self):
  if not self.authorized():return self.reply(401,{'error':'unauthorized'})
  try:
   if self.path=='/status':return self.reply(200,status(cfg()))
   if self.path=='/system':
    from nas_apps import system_snapshot
    return self.reply(200,system_snapshot(cfg()))
   for prefix,container in [('/app-logs/',False),('/container-logs/',True)]:
    if self.path.startswith(prefix):
     from nas_apps import log_text
     return self.reply(200,{'logs':log_text(cfg(),self.path[len(prefix):],container)})
   if self.path=='/sync/history':
    from nas_sync import history
    return self.reply(200,history(cfg()))
   if self.path=='/writer':return self.reply(200,WRITER.snapshot())
   if self.path.startswith('/logs/') and self.path[6:] in MODULES:return self.reply(200,{'logs':logs(cfg(),self.path[6:])})
   self.reply(404,{'error':'not found'})
  except Exception as e:self.reply(503,{'error':str(e)})
 def do_POST(self):
  if not self.authorized():return self.reply(401,{'error':'unauthorized'})
  if self.path!='/action':return self.reply(404,{'error':'not found'})
  try:
   size=int(self.headers.get('Content-Length','0'))
   if size<1 or size>4096:raise RuntimeError('Invalid request size')
   data=json.loads(self.rfile.read(size));result=action(cfg(),data);self.reply(200,{'ok':True,**(result or {})})
  except Exception as e:self.reply(400,{'error':str(e)})
class UnixServer(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):daemon_threads=True

def scheduler():
 last_backup_attempt=0
 last_mirror_attempt=0
 last_cloud_attempt=0
 last_certificate_attempt=-3600
 while True:
  try:
   c=cfg()
   if WRITER and WRITER.active:
    with lock():WRITER.monitor()
   now=datetime.datetime.now();last=read_state(c,'backup.json',{})
   if c.get('sso',{}).get('prepared') and time.time()-read_state(c,'identity-certificate.json',{}).get('checked_at',0)>86400 and time.monotonic()-last_certificate_attempt>3600:
    last_certificate_attempt=time.monotonic()
    with lock():
     from nas_sso import renew_certificate
     renew_certificate(c)
   # Do not silently keep writing after the SSD disappears or ext4 aborts its journal.
   if mounted(c):
    try:
     volume(c)
     verify_mount(c)
     options=output(['findmnt','-n','-o','OPTIONS','--target',c['mount']]).split(',')
     if 'ro' in options or 'emergency_ro' in options:raise RuntimeError('Database filesystem became read-only')
    except Exception as error:
     (Path(c['host'])/'state/deployment-blocked').write_text('Runtime storage failure: '+str(error)+'\n')
     audit(c,'storage-guard',str(error))
     with lock():stop(c,'business',remember_selection=False)
   if mounted(c) and (shutil.disk_usage(c['data']).free<c['critical_outer_free'] or shutil.disk_usage(Path(c['image']).parent).free<c.get('critical_image_outer_free',c['critical_outer_free']) or shutil.disk_usage(c['mount']).free<1024**3):
    with lock():stop(c,'business',remember_selection=False);audit(c,'capacity-guard','business stopped; image remains mounted')
  except Exception as error:
   try:audit(c,'scheduler-error',str(error))
   except Exception:pass
  time.sleep(30)

def main():
 global WRITER
 parser=argparse.ArgumentParser();parser.add_argument('command',choices=['start','stop','status','mount','unmount','backup','disk-backup','agent','logs','shutdown','wait-ready']);parser.add_argument('module',nargs='?',default='all');parser.add_argument('--purge-history',action='store_true');a=parser.parse_args();c=cfg()
 if a.purge_history and a.command not in ('backup','disk-backup'):raise RuntimeError('--purge-history only applies to backup')
 if a.command=='agent':
  from nas_writer import Writer
  WRITER=Writer(c,audit);WRITER.serve()
  path='/run/nas-control/control.sock';Path('/run/nas-control').mkdir(mode=0o750,exist_ok=True)
  if Path(path).exists():Path(path).unlink()
  with UnixServer(path,Agent) as server:
   os.chmod(path,0o660);threading.Thread(target=scheduler,daemon=True).start()
   from nas_recovery import serve as recovery_serve
   threading.Thread(target=recovery_serve,daemon=True).start()
   from nas_continuous import serve as continuous_serve
   threading.Thread(target=continuous_serve,daemon=True).start();server.serve_forever()
 elif a.command=='wait-ready':
  from nas_ready import wait_meta_ready
  modules=autostart(c) if a.module=='all' else [a.module]
  if a.module=='all' and not c['ml_enabled']:modules=[m for m in modules if m!='ml']
  wait_meta_ready(c,modules)
 elif a.command=='status':print(json.dumps(status(c),ensure_ascii=False,indent=2))
 elif a.command=='logs':print(logs(c,a.module))
 else:
  full_stop=a.command=='shutdown' or (a.command=='stop' and a.module=='all')
  if full_stop:
   from nas_recovery import pause
   pause(c)
   from nas_backup_control import status as backup_status,cancel
   current=backup_status(c)['operation']
   if current.get('kind')=='cloud' and current.get('state') in ('running','cancelling'):
    try:cancel(c,current['id'])
    except RuntimeError:pass
  with lock(blocking=full_stop):
   if a.command=='shutdown':stop(c,'all',remember_selection=False)
   elif a.command in ('backup','disk-backup'):
    if a.command=='disk-backup' and not c.get('hdd_root'):raise RuntimeError('Disk backup target is not configured')
    backup_with_mirror(c,purge_history=a.purge_history)
   elif a.command in ('start','stop'):globals()[a.command](c,a.module)
   else:globals()[a.command](c)
if __name__=='__main__':
 try:main()
 except Exception as e:print('NAS ERROR: '+str(e),file=sys.stderr);sys.exit(1)
