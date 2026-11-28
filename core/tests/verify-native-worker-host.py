#!/usr/bin/env python3
"""Opt-in sandbox/I/O proof with a 16 MiB generated fixture, never user payloads."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import argparse,json,os,subprocess,time
import nasctl,nas_storage
p=argparse.ArgumentParser();p.add_argument('--run',action='store_true');p.add_argument('--workspace',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args()
assert a.run and not a.report.exists();c=nasctl.cfg();nas_storage.verify(c)
root=a.workspace;assert root.parent==Path(c['data_storage']['mount']) and root.name.startswith('.native-io-test-') and not root.exists()
result={'state':'running'};target=root/'generated-zeroes';created=False
try:
 with nasctl.lock(shared=True):
  nasctl.volume(c);nasctl.verify_mount(c)
  mount=json.loads(subprocess.check_output(['findmnt','-J','--target',c['data'],'-o','OPTIONS'],text=True))['filesystems'][0]
  assert 'ro' in mount['options'].split(','),'Worker must see user data read-only'
  # Verify the real managed database remains reachable without changing it.
  from nas_pg_backup import details
  info=details(c);assert int(info['version'])>=140000
  group=Path('/sys/fs/cgroup')/Path('/proc/self/cgroup').read_text().strip().split('::')[1].lstrip('/')
  limits={key:(group/key).read_text().strip() for key in ('cpu.max','memory.max','memory.swap.max','io.max')}
  result['limits']=limits
  assert 'rbps=4194304' in limits['io.max'] and 'wbps=1048576' in limits['io.max']
  root.mkdir(mode=0o700);created=True
  start=time.monotonic();subprocess.run(['dd','if=/dev/zero','of='+str(target),'bs=1M','count=16','oflag=direct','conv=fsync','status=none'],check=True,timeout=90);write=time.monotonic()-start
  start=time.monotonic();subprocess.run(['dd','if='+str(target),'of=/dev/null','bs=1M','iflag=direct','status=none'],check=True,timeout=30);read=time.monotonic()-start
  assert write>=10 and read>=2,'Configured I/O limit did not constrain physical fixture traffic'
  result={'state':'PASS','limits':limits,'generated_bytes':16*1024**2,'write_seconds':write,'read_seconds':read,'io_stat':(group/'io.stat').read_text(),'checks':['real physical USB I/O limits','worker user-data mount read-only','managed storage identity','lifecycle shared lock','database healthy read-only query']}
except BaseException as error:result.update(state='FAIL',error=str(error));raise
finally:
 if created:
  nas_storage.verify(c);assert target.parent==root;target.unlink(missing_ok=True);root.rmdir()
 a.report.write_text(json.dumps(result,indent=2))
