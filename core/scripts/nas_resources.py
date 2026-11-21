"""Read-only NAS runtime (WSL/Linux) host metrics, not sums of container limits."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import datetime,os,subprocess,threading,time
from pathlib import Path
_LOCK=threading.Lock();_LAST_CPU=None;_CACHE=None;_CACHE_TIME=0

def cpu_counters(text):
 fields=next(line for line in text.splitlines() if line.startswith('cpu ')).split()[1:]
 values=[int(v) for v in fields]
 if len(values)<4:raise ValueError('CPU counters unavailable')
 # guest and guest_nice are already included in user/nice; do not count twice.
 return sum(values[:8]),values[3]+(values[4] if len(values)>4 else 0)

def cpu_usage(previous,current):
 total=current[0]-previous[0];idle=current[1]-previous[1]
 if total<=0 or idle<0 or idle>total:return None
 return round(100*(total-idle)/total,2)

def memory_usage(text):
 fields={line.split(':',1)[0]:int(line.split(':',1)[1].split()[0])*1024 for line in text.splitlines() if ':' in line}
 total=fields.get('MemTotal');available=fields.get('MemAvailable')
 if not total or available is None or not 0<=available<=total:raise ValueError('Memory counters unavailable')
 free=fields.get('MemFree');allocated=total-free if free is not None and 0<=free<=total else None
 return {'allocated_bytes':allocated,'allocated_percent':round(100*allocated/total,2) if allocated is not None else None,'state':'ready','total_bytes':total,'available_bytes':available,'used_bytes':total-available,'used_percent':round(100*(total-available)/total,2)}

def cgroup_values(path):
 fields=dict(line.split() for line in (path/'cpu.stat').read_text().splitlines())
 cpu=int(fields['usage_usec']);memory=int((path/'memory.current').read_text())
 if cpu<0 or memory<0:raise ValueError('Invalid cgroup counters')
 return cpu,memory

def group_paths(project='nas',containers=None,cgroup_root='/sys/fs/cgroup',proc_root='/proc'):
 root=Path(cgroup_root)
 if not (root/'cgroup.controllers').is_file():raise RuntimeError('cgroup v2 unavailable')
 if containers is None:
  docker=['/usr/bin/docker','--host','unix:///var/run/docker.sock']
  ids=subprocess.check_output(docker+['ps','-q','--filter','label=com.docker.compose.project='+project],text=True,stderr=subprocess.DEVNULL,timeout=5).split()
  rows=subprocess.check_output(docker+['inspect','--format','{{.State.Pid}}',*ids],text=True,stderr=subprocess.DEVNULL,timeout=5).split() if ids else []
  pids=[int(pid) for pid in rows]
 else:pids=[r['State']['Pid'] for r in containers if r['State']['Running']]
 paths=set()
 for pid in pids:
  if pid<=0:raise RuntimeError('Container is changing state')
  entry=next(line[3:] for line in Path(proc_root,str(pid),'cgroup').read_text().splitlines() if line.startswith('0::'))
  path=(root/entry.lstrip('/')).resolve()
  if path==root or not path.is_relative_to(root):raise RuntimeError('Invalid container cgroup')
  paths.add(path)
 for service in ['nas-agent.service','nas-session.service','nas-soak.service']:
  path=root/'system.slice'/service
  if path.is_dir():paths.add(path)
 # Parent cgroup counters already include descendants; never double count them.
 return sorted(p for p in paths if not any(q!=p and q in p.parents for q in paths))

def nas_counters(paths):
 if paths is None:return None,None
 cpu={};memory=0
 try:
  for path in paths:
   used,allocated=cgroup_values(path);cpu[str(path)]=used;memory+=allocated
  return cpu,memory
 except (OSError,ValueError,KeyError):return None,None

def nas_cpu_usage(previous,current,host_delta,clock_ticks):
 if previous is None or current is None or previous.keys()!=current.keys() or host_delta<=0:return None
 deltas=[current[key]-previous[key] for key in current]
 if any(delta<0 for delta in deltas):return None
 percent=100*sum(deltas)/(host_delta*1000000/clock_ticks)
 return round(percent,3) if 0<=percent<=100 else None

def snapshot(project='nas',containers=None):
 global _LAST_CPU,_CACHE,_CACHE_TIME
 with _LOCK:
  now=time.monotonic()
  if _CACHE is not None and now-_CACHE_TIME<2 and _CACHE.get('project')==project:return _CACHE
  try:paths=group_paths(project,containers)
  except (OSError,RuntimeError,ValueError,StopIteration,subprocess.SubprocessError):paths=None
  result={'scope':'nas_runtime','scope_label':'WSL / Linux 宿主资源','project':project,'scope_detail':'上限为当前 WSL/Linux 可见资源，不查询 Windows；NAS 包括本项目容器及控制服务。CPU 按全部可见核心归一化；复合内存按已分配量（含缓存）统计。宿主总量包含 NAS，不与 NAS 相加。','sampled_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
  try:
   nas_cpu,nas_memory=nas_counters(paths);counters=cpu_counters(Path('/proc/stat').read_text());sample_time=time.monotonic()
   keys=lambda value:None if value is None else set(value)
   if _LAST_CPU is None or _LAST_CPU[3]!=project or keys(_LAST_CPU[2])!=keys(nas_cpu):
    _LAST_CPU=(counters,sample_time,nas_cpu,project);time.sleep(.2)
    nas_cpu,nas_memory=nas_counters(paths);counters=cpu_counters(Path('/proc/stat').read_text());sample_time=time.monotonic()
   percent=cpu_usage(_LAST_CPU[0],counters);interval=sample_time-_LAST_CPU[1]
   nas_percent=nas_cpu_usage(_LAST_CPU[2],nas_cpu,counters[0]-_LAST_CPU[0][0],os.sysconf('SC_CLK_TCK'))
   _LAST_CPU=(counters,sample_time,nas_cpu,project)
   result['cpu']={'state':'ready' if percent is not None else 'unavailable','used_percent':percent,'host_used_percent':percent,'nas_used_percent':nas_percent,'nas_state':'ready' if nas_percent is not None else 'unavailable','logical_cores':os.cpu_count(),'sample_seconds':round(interval,3)}
  except (OSError,ValueError,StopIteration):
   nas_memory=None;result['cpu']={'state':'unavailable','used_percent':None,'nas_used_percent':None,'nas_state':'unavailable'}
  try:
   text=Path('/proc/meminfo').read_text();memory=memory_usage(text)
   allocated=memory['allocated_bytes']
   valid_nas=nas_memory is not None and 0<=nas_memory<=memory['total_bytes']
   memory.update(allocated_bytes=allocated,allocated_percent=round(100*allocated/memory['total_bytes'],2) if allocated is not None else None,nas_used_bytes=nas_memory if valid_nas else None,nas_used_percent=round(100*nas_memory/memory['total_bytes'],3) if valid_nas else None,nas_state='ready' if valid_nas else 'unavailable',accounting='allocated_including_cache')
   result['memory']=memory
  except (OSError,ValueError):result['memory']={'state':'unavailable','nas_state':'unavailable'}
  try:result['uptime_seconds']=max(0,int(float(Path('/proc/uptime').read_text().split()[0])))
  except (OSError,ValueError,IndexError):result['uptime_seconds']=None
  result['nas_scope']={'state':'ready' if paths is not None else 'unavailable','groups':len(paths) if paths is not None else None,'includes':'project_containers_and_control_services'}
  _CACHE=result;_CACHE_TIME=time.monotonic();return result
