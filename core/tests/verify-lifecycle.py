import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import hashlib,json,subprocess,urllib.request,uuid
from pathlib import Path
from nasctl import cfg,lock,stop,start,unmount,mount,mounted,inventory,verify_mount,BUSINESS,MODULES
c=cfg();initial=inventory(c);restore_modules=[m for m in BUSINESS if any(r['service'] in MODULES[m] and r['state']=='running' for r in initial)];restore_modules.sort(key=lambda m:m!='ml');results=[];op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
asset=json.loads((Path(c['root'])/'system/runtime/acceptance/immich.json').read_text());uuid.UUID(asset['asset_id'])
with lock():
 stop(c,'business')
 with op.open('http://127.0.0.1:8080/health',timeout=10) as r:assert r.status==200
 assert any(x['service']=='tailscale' and x['state']=='running' for x in inventory(c))
 results.append({'test':'stop business preserves Meta and Tailscale','result':'PASS'})
 unmount(c);assert not mounted(c)
 results.append({'test':'clean DB stop then unmount and loop detach','result':'PASS'})
 mount(c);verify_mount(c)
 for module in restore_modules:start(c,module)
 results.append({'test':'reattach same production ext4 and restart business','result':'PASS'})
password=(Path(c['host'])/'secrets/admin-password.txt').read_text().strip()
req=urllib.request.Request('http://127.0.0.1:2283/api/auth/login',data=json.dumps({'email':'admin@nas.local','password':password}).encode(),headers={'Content-Type':'application/json'})
with op.open(req,timeout=30) as r:token=json.load(r)['accessToken']
req=urllib.request.Request('http://127.0.0.1:2283/api/assets/'+asset['asset_id']+'/original',headers={'Authorization':'Bearer '+token})
with op.open(req,timeout=30) as r:assert hashlib.sha256(r.read()).hexdigest()==asset['sha256']
results.append({'test':'existing asset survives stop/unmount/remount/restart and downloads identically','result':'PASS'})
(Path(c['root'])/'system/runtime/acceptance/lifecycle.json').write_text(json.dumps(results,indent=2))
print('PASS:',len(results),'production lifecycle checks. Business restarted; management retained.')
