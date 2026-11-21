"""Application capabilities above container lifecycle; authenticated host-only metrics."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import datetime,json,re,subprocess,time,threading
APPS={
 'immich':{'name':'Immich 相册','icon':'photo','modules':['immich','ml'],'primary':['immich'],'description':'备份照片与视频，管理原件、缩略图与转码；可选智能检索和人脸识别。','purpose':'管理照片原件、上传、缩略图和转码；智能检索与人脸识别是相册的可选功能。','lifecycle':'关闭相册会同时停止它的所有功能。重新开启时恢复保存的功能设置。','features':[{'id':'intelligence','name':'智能检索与人脸识别','description':'按内容搜索照片，识别人脸；仅在相册运行时生效。','module':'ml','requires_parent':True}]},
 'files':{'name':'文件浏览','icon':'folder','modules':['files','smb'],'primary':['files','smb'],'description':'只读浏览、下载或 SMB 挂载全部文件；离线读写请使用共享同步盘。','purpose':'只读访问文件空间；离线读写请使用共享同步盘。','lifecycle':'应用开关控制全部访问入口；展开后可分别调整网页与网络挂载。','features':[{'id':'web','name':'网页浏览','description':'在浏览器里查看目录和下载文件。','module':'files'},{'id':'network-drive','name':'SMB 只读挂载','description':'在 Finder 或文件管理器挂载网络共享。','module':'smb'}]},
 'syncthing':{'name':'共享同步盘','icon':'sync','modules':['syncthing'],'primary':['syncthing'],'description':'同步设备与 NAS 的共享文件；离线可读写，保留冲突副本和历史版本。','purpose':'在设备与 NAS 的共享空间之间同步文件。冲突保留双方副本，NAS 保留历史版本。','lifecycle':'应用开关控制整个同步服务；暂停共享只暂停文件同步，绑定关系和本地文件保留。'},
 'knowledge':{'name':'私人知识库','icon':'book','modules':['knowledge'],'primary':['knowledge'],'description':'阅读、搜索 Markdown 笔记及附件；源文件由主机管理员维护。','purpose':'只读呈现 knowledge 中的 Markdown 与附件，由主机管理员维护源文件。','lifecycle':'关闭后停止阅读入口，保留全部笔记。'},
 'git':{'name':'Git 仓库','icon':'git','modules':['git'],'primary':['git'],'description':'通过授权 SSH 公钥推送、拉取 Git 仓库，保存项目版本。','purpose':'使用授权 SSH 公钥推送、拉取 Git 裸仓库；不提供交互式 shell。','lifecycle':'关闭后暂停推送和拉取，保留仓库。'},
 'platform':{'name':'零号庭院基础服务','icon':'server','modules':['meta','tailscale'],'primary':['meta','tailscale'],'description':'提供管理、认证与私人远程访问入口。','purpose':'提供管理、认证和安全的私人访问入口；管理容器不直接挂载 data。','lifecycle':'由主机维护，避免关闭管理或远程连接导致失联。','protected':True}
}
def module_running(v):return any(x['state'] in ('running','restarting','paused') for x in v.get('containers',[]))
def snapshot(c,modules,desired):
 from nasctl import MODULES
 result={}
 for key,app in APPS.items():
  primary=[modules.get(m,{'state':'unavailable'}) for m in app['primary']]
  active=any(module_running(v) for v in primary)
  state='ready' if all(v['state']=='ready' for v in primary) else 'stopped' if all(v['state']=='stopped' for v in primary) else 'partial' if any(v['state']=='ready' for v in primary) else 'starting' if active else 'unavailable'
  entry={k:v for k,v in app.items() if k not in ('modules','primary','features')};entry.update(id=key,state=state,running=active,services=sum((MODULES[m] for m in app['modules']),[]),features=[],log_sources=[{'id':service,'name':{'immich-server':'相册服务','immich-machine-learning':'智能检索','database':'数据库','cache':'缓存','files':'网页浏览','smb':'网络挂载','syncthing':'同步服务','knowledge':'知识库','git':'Git 服务','meta':'零号庭院管理','tailscale':'远程连接'}.get(service,service)} for module in app['modules'] for service in MODULES[module]])
  if key=='immich':entry['log_sources'].sort(key=lambda s:['immich-server','immich-machine-learning','database','cache'].index(s['id']))
  for feature in app.get('features',[]):
   m=feature['module'];v=modules.get(m,{'state':'unavailable'});needs_parent=feature.get('requires_parent',False)
   entry['features'].append({k:value for k,value in feature.items() if k not in ('module','requires_parent')}|{'enabled':desired.get('ml_preference',True) if m=='ml' else module_running(v),'active':module_running(v),'effective':v['state']=='ready' and module_running(v) and (not needs_parent or state=='ready'),'available':not needs_parent or state=='ready','state':v['state'],'hint':'先开启相册；保留上次设置' if needs_parent and state!='ready' else '功能已就绪' if v['state']=='ready' else '已关闭' if v['state']=='stopped' else '就绪待确认'})
  result[key]=entry
 return result

def power(c,key,enabled):
 from nasctl import start,stop
 if key not in APPS or not isinstance(enabled,bool):raise RuntimeError('Invalid application control')
 app=APPS[key]
 if app.get('protected'):raise RuntimeError('基础服务由主机维护')
 for module in app['primary']:(start if enabled else stop)(c,module)

def feature(c,key,name,enabled):
 from nasctl import start,stop,inventory
 if key not in APPS or not isinstance(enabled,bool):raise RuntimeError('Invalid application feature')
 item=next((f for f in APPS[key].get('features',[]) if f['id']==name),None)
 if item is None:raise RuntimeError('Unknown application feature')
 if enabled and item.get('requires_parent'):
  active={r['service']:r for r in inventory(c)}
  if not all(active.get(s,{}).get('state')=='running' and active[s]['health']=='healthy' for s in ('immich-server','database','cache')):raise RuntimeError('请先开启相册，再启用智能检索')
 (start if enabled else stop)(c,item['module'])

_CACHE={};_LOCK=threading.Lock()
def system_snapshot(c):
 from nasctl import DOCKER,output,MODULES
 with _LOCK:
  if time.monotonic()-_CACHE.get('time',-100)<5:return _CACHE['value']
  ids=output(DOCKER+['ps','-aq','--filter','label=com.docker.compose.project='+c['project']]).split()
  inspected=json.loads(output(DOCKER+['inspect']+ids)) if ids else []
  running=[r['Id'] for r in inspected if r['State']['Running']];samples={};stats_error=False
  if running:
   try:
    raw=subprocess.check_output(DOCKER+['stats','--no-stream','--format','{{json .}}']+running,text=True,timeout=20)
    samples={r['ID']:r for line in raw.splitlines() if (r:=json.loads(line))}
   except (subprocess.SubprocessError,ValueError):stats_error=True
  def bytes_value(value):
   m=re.fullmatch(r'([0-9.]+)\s*([kMGT]?i?B)',value.strip())
   return round(float(m[1])*{'B':1,'kB':1000,'MB':1000**2,'GB':1000**3,'TB':1000**4,'KiB':1024,'MiB':1024**2,'GiB':1024**3,'TiB':1024**4}[m[2]]) if m else None
  owner={service:(key,app['name']) for key,app in APPS.items() for module in app['modules'] for service in MODULES[module]}
  now=datetime.datetime.now(datetime.timezone.utc);rows=[]
  for r in inspected:
   service=r['Config']['Labels'].get('com.docker.compose.service','unknown');app,name=owner.get(service,('unknown','未归属'))
   sample=next((v for k,v in samples.items() if r['Id'].startswith(k)),{}) if r['State']['Running'] else {}
   memory=sample.get('MemUsage','').split('/')
   try:uptime=max(0,int((now-datetime.datetime.fromisoformat(r['State']['StartedAt'].replace('Z','+00:00'))).total_seconds())) if r['State']['Running'] else None
   except ValueError:uptime=None
   rows.append({'name':r['Name'].lstrip('/'),'service':service,'app':app,'application':name,'state':r['State']['Status'],'health':r['State'].get('Health',{}).get('Status','unknown'),'started_at':r['State']['StartedAt'],'uptime_seconds':uptime,'cpu_percent':float(sample['CPUPerc'].rstrip('%')) if sample.get('CPUPerc') else None,'memory_bytes':bytes_value(memory[0]) if len(memory)==2 else None,'memory_limit_bytes':bytes_value(memory[1]) if len(memory)==2 else None,'pids':sample.get('PIDs'),'network_io':sample.get('NetIO'),'block_io':sample.get('BlockIO'),'image':r['Config']['Image'],'restart_count':r.get('RestartCount',0),'mounts':[{'source':m.get('Source',m.get('Name','')),'target':m['Destination'],'read_only':not m.get('RW',False),'type':m['Type']} for m in r.get('Mounts',[])],'ports':[{'container':port,'address':binding['HostIp'],'port':binding['HostPort']} for port,bindings in r['NetworkSettings'].get('Ports',{}).items() for binding in bindings or []]})
  result={'sampled_at':now.isoformat(),'stats_available':not stats_error,'containers':sorted(rows,key=lambda r:(r['application'],r['service'])),'scope':'仅 NAS 项目；CPU 可超过 100%（多核），内存为 Docker 即时工作集统计，停止或采样失败时显示未知。'}
  _CACHE.update(time=time.monotonic(),value=result);return result

def clean_log(text):
 text=re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)','',text)
 text=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',text)
 return ''.join(ch for ch in text if ch in '\n\t' or ord(ch)>=32)

def log_text(c,key,container=False):
 from nasctl import DOCKER,output,MODULES
 if container:
  if key not in sum(MODULES.values(),[]):raise RuntimeError('Unknown NAS container service')
  services=[key]
 else:
  if key not in APPS:raise RuntimeError('Unknown application')
  services=sum((MODULES[m] for m in APPS[key]['modules']),[])
 # No environment, inspect credentials or arbitrary commands in this endpoint.
 return clean_log(output(DOCKER+['compose','--project-name',c['project'],'--env-file',c['host']+'/config/compose.env','-f',c['host']+'/config/nas.yml','logs','--no-color','--tail','100']+services))[-64000:]
