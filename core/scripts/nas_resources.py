"""Read-only NAS runtime (WSL/Linux) host metrics, not sums of container limits."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import datetime,json,os,subprocess,threading,time
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
 return {'state':'ready','total_bytes':total,'available_bytes':available,'used_bytes':total-available,'used_percent':round(100*(total-available)/total,2)}

def snapshot():
 global _LAST_CPU,_CACHE,_CACHE_TIME
 with _LOCK:
  now=time.monotonic()
  if _CACHE is not None and now-_CACHE_TIME<2:return _CACHE
  result={'scope':'nas_runtime','scope_label':'WSL2 整体环境','scope_detail':'CPU、内存与运行时间按整个 WSL/Linux 环境统计，包含 NAS 以外的进程；CPU 按全部可见核心归一化，内存已用为总量减可用量，不是 NAS 容器占比或 Windows 整机占比。','sampled_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
  try:
   counters=cpu_counters(Path('/proc/stat').read_text());sample_time=time.monotonic()
   if _LAST_CPU is None:
    _LAST_CPU=(counters,sample_time);time.sleep(.15);counters=cpu_counters(Path('/proc/stat').read_text());sample_time=time.monotonic()
   percent=cpu_usage(_LAST_CPU[0],counters);interval=sample_time-_LAST_CPU[1];_LAST_CPU=(counters,sample_time)
   result['cpu']={'state':'ready' if percent is not None else 'unavailable','used_percent':percent,'logical_cores':os.cpu_count(),'sample_seconds':round(interval,3)}
  except (OSError,ValueError,StopIteration):result['cpu']={'state':'unavailable','used_percent':None}
  try:result['memory']=memory_usage(Path('/proc/meminfo').read_text())
  except (OSError,ValueError):result['memory']={'state':'unavailable'}
  try:result['uptime_seconds']=max(0,int(float(Path('/proc/uptime').read_text().split()[0])))
  except (OSError,ValueError,IndexError):result['uptime_seconds']=None
  _CACHE=result;_CACHE_TIME=time.monotonic();return result


_VOLUME_CACHE={};_VOLUME_LOCK=threading.Lock()
def windows_volume_usage(path,expected_guid):
    """Read a registered Windows volume even when WSL has not mounted its drive."""
    parts=Path(path).parts
    if len(parts)<4 or parts[:2]!=('/','mnt') or len(parts[2])!=1 or not parts[2].isalpha() or not expected_guid:return {'state':'unavailable'}
    key=(parts[2].lower(),expected_guid.lower())
    with _VOLUME_LOCK:
        cached=_VOLUME_CACHE.get(key)
        if cached and time.monotonic()-cached[0]<30:return cached[1]
        try:
            code="[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Volume -DriveLetter '"+key[0]+"' -ErrorAction Stop | Select-Object UniqueId,Size,SizeRemaining | ConvertTo-Json -Compress"
            raw=subprocess.check_output(['/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe','-NoProfile','-NonInteractive','-Command',code],text=True,stderr=subprocess.DEVNULL,timeout=5)
            volume=json.loads(raw);total=int(volume['Size']);free=int(volume['SizeRemaining'])
            if expected_guid.lower() not in volume['UniqueId'].lower() or total<=0 or not 0<=free<=total:raise ValueError('Wrong registered volume or invalid counters')
            result={'state':'ready','total':total,'free':free,'used':total-free}
        except (OSError,subprocess.SubprocessError,ValueError,KeyError):result={'state':'unavailable'}
        _VOLUME_CACHE[key]=(time.monotonic(),result);return result
