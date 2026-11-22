#!/usr/bin/env python3
"""Exercise concurrent UI actions with deferred requests; never controls live apps."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
import ast
from js_runner import run_node
root=Path(__file__).resolve().parents[2]
script=r'''
const vm=require('vm'),assert=require('assert/strict'),fs=require('fs'),p=JSON.parse(fs.readFileSync(0,'utf8'));
(async()=>{
const elements={};function node(tag='div'){return {tag,dataset:{},style:{},attrs:{},children:[],textContent:'',setAttribute(k,v){this.attrs[k]=v},append(...v){this.children.push(...v)},replaceChildren(...v){this.children=v}}}
const ctx={window:{},document:{createElement:node,querySelector:s=>elements[s]??=node(),querySelectorAll:()=>[]},location:{hash:'',protocol:'https:',hostname:'test'},setInterval(){}};
vm.createContext(ctx);vm.runInContext(p.common+'\n'+p.dashboard.replace('decorateIcons();refresh();setInterval(refresh,30000);setInterval(tickUptime,1000);setInterval(()=>{if(appsTransitioning())refresh()},1500);',''),ctx);
vm.runInContext(`
const fixture={docker:'ready',data_access:{state:'verified',rules:[]},apps:{},operations:{},syncthing:{},git:{}};
for(const id of ['files','joplin'])fixture.apps[id]={id,name:id,description:'test',services:[],features:[],running:false,state:'stopped'};
fixture.apps.files.features=[{id:'test',name:'test',available:true,enabled:false,effective:false,state:'stopped'}];
window.viewStatus=fixture;const deferred=[];request=async(path,data)=>new Promise((resolve,reject)=>deferred.push({data,resolve,reject}));refresh=async()=>renderApps(fixture);renderApps(fixture);
`,ctx);
const run=s=>vm.runInContext(s,ctx);let first=run("submitAction({action:'app-power',app:'files',enabled:true})"),second=run("submitAction({action:'app-power',app:'joplin',enabled:true})");
assert.equal(run('deferred.length'),2);assert.equal(run('actionPending'),false);assert.equal(run('appViews.files.status.children[0].children[1].textContent'),'启动中');assert.equal(run('appViews.joplin.status.children[0].disabled'),true);
await run("submitAction({action:'app-power',app:'files',enabled:true})");assert.equal(run('deferred.length'),2);
run("fixture.apps.files.running=true;fixture.apps.files.state='ready';deferred[0].resolve({})");await first;assert.equal(run('appViews.files.status.children[0].children[1].textContent'),'运行中');assert.equal(run('appViews.files.status.children[0].disabled'),false);assert.equal(run('appViews.joplin.status.children[0].disabled'),true);
run('deferred[1].reject(new Error("fixture error"))');await second;assert.equal(run('appViews.joplin.error.textContent'),'fixture error');assert.equal(elements['#message']?.textContent||'','');
run('renderApps(fixture);appViews.files.features.children[0].children[1].children[0].checked=true;appViews.files.features.children[0].children[1].children[0].onchange()');
assert.equal(run('appViews.files.features.children[0].children[1].children.length'),2);assert.equal(run('appViews.files.features.children[0].children[1].children[1].checked'),false);
run("fixture.apps.files.features[0].effective=true;fixture.apps.files.features[0].state='ready';deferred[2].resolve({})");await new Promise(setImmediate);
assert.equal(run('appViews.files.features.children[0].children[1].children[0].checked'),true);assert.equal(run('pendingApps.size'),0);assert.equal(elements['#message']?.textContent||'','');
console.log('PASS: independent concurrent actions, per-app busy states, duplicate suppression, inline failure, feature waits for effectiveness, no success toast');
})().catch(e=>{console.error(e.stack);process.exit(1)});
'''
base=next(ast.literal_eval(n.value) for n in ast.parse((root/'core/web/app.py').read_text()).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='JS' for t in n.targets))
print(run_node(script,{'common':base+(root/'core/web/management.js').read_text(),'dashboard':(root/'core/web/dashboard.js').read_text()}).strip())
