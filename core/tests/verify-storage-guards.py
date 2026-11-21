import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import importlib.util,json,subprocess
from pathlib import Path
spec=importlib.util.spec_from_file_location('nasctl',Path(__file__).resolve().parents[1]/'scripts/nasctl.py');nas=importlib.util.module_from_spec(spec);spec.loader.exec_module(nas)
c=nas.cfg();results=[]
def reject(name,fn):
 try:fn()
 except Exception as e:results.append({'test':name,'result':'PASS','rejected':str(e)});return
 raise AssertionError('Guard did not reject '+name)
wrong=dict(c,volume_guid='00000000-0000-0000-0000-000000000000');reject('wrong SSD identity',lambda:nas.volume(wrong))
wrong=dict(c,image_uuid='00000000-0000-0000-0000-000000000000');reject('wrong filesystem UUID',lambda:nas.mount(wrong))
wrong=dict(c,image='/mnt/d/nas/system/runtime/storage-probes/missing.img');reject('missing image',lambda:nas.mount(wrong))
wrong=dict(c,mount='/run/nas/not-mounted');reject('empty mount fallback',lambda:nas.verify_mount(wrong))
reject('unmount running DB',lambda:nas.unmount(c))
wrong=dict(c,critical_outer_free=10**18);reject('critical free-space guard',lambda:nas.volume(wrong))
# Read-only container mount must reject filesystem writes independently of HTTP permissions.
r=subprocess.run(nas.DOCKER+['exec','nas-files-1','python','-c',"open('/data/shared/forbidden-write','w').write('x')"],capture_output=True,text=True)
assert r.returncode!=0 and not (Path(c['data'])/'shared/forbidden-write').exists();results.append({'test':'read-only Docker data mount','result':'PASS'})
Path(c['root']+'/system/runtime/acceptance/storage-guards.json').write_text(json.dumps(results,indent=2))
print('PASS:',len(results),'storage rejection checks; production data not changed.')
