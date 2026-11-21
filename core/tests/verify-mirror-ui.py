#!/usr/bin/env python3
"""Execute actual backup choice JavaScript with simulated DOM; no backup requests."""
import sys
sys.dont_write_bytecode=True
import json,subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[2]
script=(root/'core/web/dashboard.js').read_text()
harness=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');const js=JSON.parse(fs.readFileSync(0,'utf8')).js;let opened=false,calls=[];
const dialog={showModal(){opened=true},close(){opened=false}};
const ctx={console,document:{querySelector:s=>{assert.equal(s,'#backup-choice');return dialog},querySelectorAll:()=>[]},window:{},setInterval(){},request:()=>new Promise(()=>{}),capture:async d=>calls.push(JSON.parse(JSON.stringify(d)))};
vm.createContext(ctx);vm.runInContext(js+';submitAction=capture;globalThis.api={act,confirmBackup,cancelBackup}',ctx);
(async()=>{await ctx.api.act('backup');assert(opened);assert.equal(calls.length,0);ctx.api.cancelBackup();assert(!opened);assert.equal(calls.length,0);
await ctx.api.act('backup');await ctx.api.confirmBackup(false);assert.equal(calls[0].action,'backup');assert.equal(calls[0].purge_history,false);
await ctx.api.act('hdd-backup');await ctx.api.confirmBackup(true);assert.equal(calls[1].action,'hdd-backup');assert.equal(calls[1].purge_history,true);assert(!opened);
await ctx.api.act('stop','business');assert.equal(calls[2].action,'stop');assert.equal(calls[2].module,'business');assert(!Object.hasOwn(calls[2],'purge_history'));
console.log('PASS: no backup before choice; cancel sends nothing; preserve/purge choices explicit; unrelated actions unchanged')})().catch(e=>{console.error(e);process.exit(1)});
'''
r=subprocess.run(['sudo','-n','docker','exec','-i','nas-immich-server-1','node','-e',harness],input=json.dumps({'js':script}),text=True,capture_output=True)
if r.returncode:raise RuntimeError(r.stderr)
print(r.stdout.strip())
