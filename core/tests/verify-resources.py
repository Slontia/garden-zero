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
check('memory excludes reclaimable availability, not just free bytes',m['used_bytes']==400*1024 and m['used_percent']==40)
try:memory_usage('MemTotal: 1000 kB\nMemFree: 100 kB\n')
except ValueError:checks.append('missing memory availability is not fabricated')
else:raise AssertionError('missing memory availability')
s=snapshot();check('live scope distinguishes runtime from Windows and containers',s['scope']=='nas_runtime' and 'WSL' in s['scope_detail'])
check('real CPU sample has bounded aggregate percentage',s['cpu']['state']=='ready' and 0<=s['cpu']['used_percent']<=100 and s['cpu']['logical_cores']>=1)
check('real memory sample has coherent capacity',s['memory']['used_bytes']+s['memory']['available_bytes']==s['memory']['total_bytes'])
check('real environment uptime available',isinstance(s['uptime_seconds'],int) and s['uptime_seconds']>0)
from unittest.mock import patch
import json,subprocess
import nas_resources as resources
resources._VOLUME_CACHE.clear()
with patch.object(resources.subprocess,'check_output',return_value=json.dumps({'UniqueId':'Volume{fixture-volume}','Size':1000,'SizeRemaining':600})) as query:
 check('Windows backup volume can be measured without mounting WSL drive',resources.windows_volume_usage('/mnt/z/nas-backup','fixture-volume')['used']==400)
 resources.windows_volume_usage('/mnt/z/nas-backup','fixture-volume');check('Windows volume query is cached',query.call_count==1)
 check('wrong Windows backup volume is not reported',resources.windows_volume_usage('/mnt/z/nas-backup','wrong-volume')['state']=='unavailable')
check('invalid Windows drive path fails closed',resources.windows_volume_usage('/untrusted/path','fixture-volume')['state']=='unavailable')
print('PASS:',len(checks),'runtime resource counter checks; no host settings changed')
