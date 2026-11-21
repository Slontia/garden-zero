#!/usr/bin/env python3
"""Check authenticated status contract and execute the delivered dashboard JavaScript."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import http.cookiejar,json,re,subprocess,urllib.request
from pathlib import Path
root=Path(__file__).resolve().parents[2];host=Path('/mnt/c/nas/system')
op=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def request(path,data=None):
 r=urllib.request.Request('http://127.0.0.1:8080'+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json','X-NAS-Request':'1'})
 with op.open(r,timeout=30) as response:return response.read().decode()
request('/api/login',{'username':'admin','password':(host/'secrets/admin-password.txt').read_text().strip()})
try:
 page=request('/');status=json.loads(request('/api/status'))
 assert status['file_access']['mode']=='full_sync' and status['file_access']['active_writer'] is None
 assert status['file_access']['writer_selection']=='disabled'
 assert status['file_access']['write_roots']==['shared']
 assert any(d['os']=='macOS' for d in status['file_access']['devices'])
 assert status['syncthing']['mode']=='full_sync' and status['syncthing']['folder_count']==1
 assert status['data_access']['state']=='verified' and len(status['data_access']['mounts'])==6
 assert status['resources']['scope']=='nas_runtime' and status['resources']['cpu']['state']=='ready'
 assert 0<=status['resources']['cpu']['used_percent']<=100 and status['resources']['memory']['used_bytes']<=status['resources']['memory']['total_bytes']
 assert status['resources']['uptime_seconds']>0
 assert 'overdue' in status['backup'] and 'window_seconds' in status['observation']
 for panel in ('access','observation','backup'):assert 'id="'+panel+'"' in page
 js=re.search(r'<script>(.*?)</script>',page,re.S).group(1)
 harness=r'''const vm=require('vm'),fs=require('fs');const p=JSON.parse(fs.readFileSync(0,'utf8'));(async()=>{for(const overdue of [false,true]){const elements={};function node(){return {textContent:'',value:'',dataset:{},style:{},children:[],setAttribute(k,v){this[k]=v},append(...items){this.children.push(...items)},replaceChildren(...items){this.children=items}}}const d=structuredClone(p.status);d.backup.overdue=overdue;d.storage_blocked=overdue;d.backup.schedule_enabled=true;const ctx={window:{},document:{createElement:node,createTextNode:node,querySelector:s=>elements[s]??=(node()),querySelectorAll:()=>[]},location:{protocol:'https:',hostname:'test.example'},setInterval(){},fetch:async()=>({status:200,ok:true,json:async()=>d})};vm.createContext(ctx);vm.runInContext(p.js,ctx);await new Promise(setImmediate);if(elements['#message']?.textContent)throw Error(elements['#message'].textContent);if(elements['#modules'].children.length!==6)throw Error('Application groups missing');if(elements['#permission-status'].textContent!=='容器挂载已核对')throw Error('Ownership validation missing');if(!elements['#access'].textContent.includes('完整本地副本')||!elements['#access'].textContent.includes('冲突副本'))throw Error('Missing readonly/writer mount status');if(!elements['#access'].textContent.includes('本机位置'))throw Error('Local location missing');if(!elements['#observation'].textContent.includes('探针次数'))throw Error('Missing observation');if(elements['#start-all'].disabled!==overdue||elements['#backup-now'].disabled!==overdue||elements['#storage-alert'].hidden===overdue)throw Error('Storage protection controls mismatch');if(!elements['#backup'].textContent.includes(overdue?'已逾期':'已启用'))throw Error('Overdue backup not visible')}console.log('PASS: authenticated Meta status and delivered JavaScript render current/pending/overdue states')})().catch(e=>{console.error(e.message);process.exit(1)})'''
 subprocess.run(['sudo','-n','/usr/bin/docker','--host','unix:///var/run/docker.sock','exec','-i','nas-immich-server-1','node','-e',harness],input=json.dumps({'status':status,'js':js}),text=True,check=True)
 (root/'system/runtime/acceptance/meta-status.json').write_text(json.dumps({'state':'PASS','checks':['authenticated status contract','deployed dashboard JavaScript executes','full-copy shared space and pairing controls','local location and conflict status','observation progress','overdue backup visible','storage protection banner and disabled start/backup controls'],'limitations':['DOM stub execution, not a visual browser screenshot']},indent=2))
finally:request('/api/logout',{})
