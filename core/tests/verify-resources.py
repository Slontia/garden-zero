#!/usr/bin/env python3
"""Validate host counter interpretation and actual read-only runtime sample."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
from nas_resources import cpu_counters,cpu_usage,memory_usage,snapshot
checks=[]
def check(name,value):
 if not value:raise AssertionError(name)
 checks.append(name)
check('guest CPU time not counted twice',cpu_counters('cpu 10 2 5 70 3 4 5 1 9 2\ncpu0 1 2 3 4')==(100,73))
check('aggregate CPU uses busy share including steal',cpu_usage((100,70),(200,130))==40)
check('CPU reset or zero intervals remain unknown',cpu_usage((100,70),(100,70)) is None and cpu_usage((100,70),(90,60)) is None)
check('impossible idle deltas remain unknown',cpu_usage((100,50),(110,70)) is None)
m=memory_usage('MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 600 kB\nCached: 400 kB\n')
check('memory excludes reclaimable availability, not just free bytes',m['used_bytes']==400*1024 and m['used_percent']==40 and m['allocated_bytes']==900*1024)
try:memory_usage('MemTotal: 1000 kB\nMemFree: 100 kB\n')
except ValueError:checks.append('missing memory availability is not fabricated')
else:raise AssertionError('missing memory availability')
s=snapshot();check('live scope distinguishes runtime from Windows and containers',s['scope']=='nas_runtime' and 'WSL' in s['scope_detail'])
check('real CPU sample has bounded aggregate percentage',s['cpu']['state']=='ready' and 0<=s['cpu']['used_percent']<=100 and s['cpu']['logical_cores']>=1)
check('real memory sample has coherent capacity',s['memory']['used_bytes']+s['memory']['available_bytes']==s['memory']['total_bytes'])
check('real environment uptime available',isinstance(s['uptime_seconds'],int) and s['uptime_seconds']>0)
from unittest.mock import patch
import tempfile,os,subprocess
import nas_resources as resources
from pathlib import Path
check('NAS CPU uses the same host-wide tick denominator',resources.nas_cpu_usage({'one':100000},{'one':300000},400,100)==5)
check('multiple NAS groups are summed without per-core inflation',resources.nas_cpu_usage({'one':0,'two':0},{'one':100000,'two':100000},400,100)==5)
check('new or restarted cgroup CPU requires a fresh baseline',resources.nas_cpu_usage({'old':0},{'new':100000},400,100) is None and resources.nas_cpu_usage({'one':1000},{'one':0},400,100) is None)
check('missing NAS telemetry stays unknown',resources.nas_cpu_usage(None,{},400,100) is None and resources.nas_counters(None)==(None,None))
check('an observed empty NAS group set is a genuine zero',resources.nas_counters([])==({},0))
with tempfile.TemporaryDirectory() as temp:
 path=Path(temp);(path/'cpu.stat').write_text('usage_usec 1200\nuser_usec 1000\nsystem_usec 200\n');(path/'memory.current').write_text('4096\n')
 check('NAS memory includes all memory charged to its group',resources.cgroup_values(path)==(1200,4096))
 (path/'memory.current').write_text('-1\n')
 try:resources.cgroup_values(path)
 except ValueError:checks.append('invalid cgroup memory refused')
 else:raise AssertionError('negative memory accepted')
check('composite host memory includes allocated cached pages',0<=s['memory']['allocated_bytes']<=s['memory']['total_bytes'])
with tempfile.TemporaryDirectory() as temp:
 base=Path(temp);root=base/'cgroup';proc=base/'proc';root.mkdir();(root/'cgroup.controllers').touch()
 for pid,group in [(123,'/containers/one/child'),(124,'/containers/one')]:
  (proc/str(pid)).mkdir(parents=True);(proc/str(pid)/'cgroup').write_text('0::'+group+'\n');(root/group.lstrip('/')).mkdir(parents=True,exist_ok=True)
 containers=[{'State':{'Running':True,'Pid':123}},{'State':{'Running':True,'Pid':124}}]
 check('ancestor cgroup avoids double counting descendant usage',resources.group_paths(containers=containers,cgroup_root=root,proc_root=proc)==[root/'containers/one'])
 with patch.object(resources.subprocess,'check_output',side_effect=['container-id\n','123\n']) as query:
  resources.group_paths('fixture-project',cgroup_root=root,proc_root=proc)
  check('container discovery is restricted to the NAS project label','label=com.docker.compose.project=fixture-project' in query.call_args_list[0].args[0])
 (proc/'123/cgroup').write_text('0::/\n')
 try:resources.group_paths(containers=[containers[0]],cgroup_root=root,proc_root=proc)
 except RuntimeError:checks.append('container root cannot be mistaken for the whole host')
 else:raise AssertionError('host cgroup attributed to NAS')
print('PASS:',len(checks),'runtime and NAS counter checks; no host settings changed')
