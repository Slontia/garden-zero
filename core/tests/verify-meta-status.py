#!/usr/bin/env python3
"""Read-only four-page contract and delivered JavaScript checks, including stopped apps."""
import sys
sys.dont_write_bytecode=True
import http.cookiejar,json,re,urllib.request
from pathlib import Path
from js_runner import run_node
root=Path(__file__).resolve().parents[2];host=Path('/mnt/c/nas/system')
op=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def request(path,data=None):
 r=urllib.request.Request('http://127.0.0.1:8080'+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json','X-NAS-Request':'1'})
 with op.open(r,timeout=30) as response:return response.read().decode()
request('/api/login',{'username':'admin','password':(host/'secrets/admin-password.txt').read_text().strip()})
try:
 pages={name:request('/'+name) for name in ['apps','backups','containers','logs']};status=json.loads(request('/api/status'))
 assert status['data_access']['state']=='verified'
 assert status['resources']['scope']=='nas_runtime' and 0<=status['resources']['cpu']['used_percent']<=100
 for name,page in pages.items():
  nav=re.search(r'<nav class="side-nav".*?</nav>',page,re.S).group()
  assert re.findall(r'href="(/[^"#]+)"',nav)==['/apps','/backups','/containers','/logs']
  assert nav.count('aria-current="page"')==1
 assert 'id="modules"' in pages['apps'] and 'id="backup-now"' not in pages['apps']
 assert 'id="backups-page"' in pages['backups'] and 'id="resource-disks"' not in pages['backups']
 for removed in ['data-permissions','permission-path','observation','resource-grid','runtime-clock']:
  assert 'id="'+removed+'"' not in pages['apps']
 scripts={name:re.search(r'<script>(.*?)</script>',page,re.S).group(1) for name,page in pages.items() if name in ('apps','backups')}
 harness=r'''
const vm=require('vm'),fs=require('fs'),assert=require('assert/strict'),p=JSON.parse(fs.readFileSync(0,'utf8'));
(async()=>{for(const name of ['apps','backups'])for(const blocked of [false,true]){const elements={};function node(tag='div'){return {tag,textContent:'',value:'',dataset:{},style:{},attrs:{},children:[],setAttribute(k,v){this.attrs[k]=v},append(...x){this.children.push(...x)},replaceChildren(...x){this.children=x}}}function text(n){return (n.textContent||'')+n.children.map(text).join(' ')}const d=structuredClone(p.status);d.storage_blocked=blocked;let ctx={window:{},document:{createElement:node,createTextNode:t=>Object.assign(node(),{textContent:t}),querySelector:s=>elements[s]??=node(),querySelectorAll:()=>[]},location:{protocol:'https:',hostname:'fixture.example',hash:''},setInterval(){},fetch:async()=>({status:200,ok:true,json:async()=>d})};vm.createContext(ctx);vm.runInContext(p.scripts[name],ctx);await new Promise(setImmediate);assert.equal(elements['#message']?.textContent||'','');assert.equal(elements['#storage-alert'].hidden,!blocked);
if(name==='apps'){assert.equal(elements['#modules'].children.length,Object.keys(d.apps).length);assert.match(elements['#resource-uptime'].textContent,/^\d{2,}:\d{2}:\d{2}$/);assert.equal(ctx.formatUptime(90061),'25:01:01');assert(!elements['#data-permissions']);assert(text(elements['#git-details']).includes('SSH 公钥'));assert(text(elements['#access']).includes('完整本地副本'));assert.equal(elements['#start-all'].disabled,blocked);let unknown=structuredClone(d);unknown.resources.cpu={state:'unavailable'};unknown.resources.memory={state:'unavailable'};ctx.dashboardStats(unknown);assert(text(elements['#resource-cpu']).includes('—'));assert.equal(elements['#resource-cpu'].children[1].attrs['aria-valuenow'],undefined)}else{assert.equal(elements['#backup-now'].disabled,blocked);assert.equal(elements['#mirror-now'].disabled,blocked||!d.hdd.path);assert(text(elements['#mirror-details']).includes('完整历史'));assert(!elements['#resource-disks'])}}
console.log('PASS: four independent pages; app and backup rendering with protected/stopped/unknown states; compact scope/uptime; Git details; controls preserved')})().catch(e=>{console.error(e.stack);process.exit(1)});
'''
 print(run_node(harness,{'status':status,'scripts':scripts}).strip())
 (root/'system/runtime/acceptance/meta-status.json').write_text(json.dumps({'state':'PASS','checks':['four separate workspace routes','exactly four links and one active page','app and backup JavaScript execution','stopped applications render without starting them','compact WSL metrics and unknown values','Git information in app details','backup controls and history policy retained'],'limitations':['VM DOM stubs; real Chrome acceptance is separate']},indent=2))
finally:request('/api/logout',{})
