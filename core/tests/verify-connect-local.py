#!/usr/bin/env python3
"""Run the actual browser enrollment logic against a new isolated real Syncthing instance."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import datetime,importlib.util,json,secrets,socket,subprocess,time,uuid,xml.etree.ElementTree as ET
from pathlib import Path
from nasctl import cfg,DOCKER

def run(args,**kw):
 r=subprocess.run(DOCKER+args,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**kw)
 if r.returncode:raise RuntimeError(r.stderr[-2000:] or 'Docker test command failed')
 return r
def main():
 c=cfg();root=Path(c['root']);host=Path(c['host'])/'state'/('connect-check-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'));(host/'secrets').mkdir(parents=True,mode=0o700);(host/'state').mkdir();(host/'data').mkdir();(host/'data/nonempty').mkdir();(host/'data/nonempty/existing.txt').write_text('preserve user content');(host/'secrets/admin-password.txt').write_text(secrets.token_urlsafe(24))
 versions=json.loads((root/'system/config/versions.json').read_text());spec=importlib.util.spec_from_file_location('prep',Path(__file__).resolve().parents[1]/'scripts/prepare-services.py');prep=importlib.util.module_from_spec(spec);spec.loader.exec_module(prep);prep.prepare_syncthing({'host':str(host)},versions)
 config=host/'state/syncthing';tree=ET.parse(config/'config.xml');tree.find('gui/address').text='127.0.0.1:19384';options=tree.getroot().find('options')
 for name in ('globalAnnounceEnabled','localAnnounceEnabled','relaysEnabled','natEnabled'):options.find(name).text='false'
 for el in list(options.findall('listenAddress')):options.remove(el)
 ET.SubElement(options,'listenAddress').text='tcp://127.0.0.1:0';tree.write(config/'config.xml');key=tree.findtext('gui/apikey')
 with socket.socket() as probe:
  probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);probe.bind(('127.0.0.1',19384))
 name='nas-connect-check-'+uuid.uuid4().hex[:8];created=False
 try:
  run(['create','--name',name,'--network','host','--read-only','--tmpfs','/tmp:size=16m','--tmpfs','/db:size=64m','-e','STHOMEDIR=','-e','STGUIADDRESS=127.0.0.1:19384','--mount',f'type=bind,src={config},dst=/config','--mount',f'type=bind,src={host}/data,dst=/fixture/data','--entrypoint','/bin/syncthing',versions['SYNCTHING_IMAGE']['image'],'serve','--config=/config','--data=/db','--no-browser','--no-restart','--no-upgrade']);created=True;run(['start',name])
  import urllib.request
  opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
  for _ in range(40):
   try:
    with opener.open(urllib.request.Request('http://127.0.0.1:19384/rest/system/status',headers={'X-API-Key':key}),timeout=2):break
   except Exception:time.sleep(.25)
  image=json.loads(run(['inspect','nas-immich-server-1']).stdout)[0]['Image']
  js=(root/'core/web/connect.js').read_text()+r'''
const fs=require('fs'),assert=require('assert/strict');const p=JSON.parse(fs.readFileSync(0,'utf8'));
(async()=>{const client=new LocalSyncthing(19384,p.key);const nas={device_id:p.nas,folder_id:'nas-shared',label:'fixture',address:'tcp://127.0.0.1:9'};let registered=0;
const register=async(data)=>{const f=await client.api('config/folders/nas-shared');assert(f.paused||registered>0);assert.equal(data.action,'sync-pair');assert(!JSON.stringify(data).includes(p.key));registered++};
await assert.rejects(enrollLocal(client,nas,'/fixture/data/nonempty',register));assert.equal((await client.api('config/folders')).length,0);
await enrollLocal(client,nas,'/fixture/data/connected',register);let f=await client.api('config/folders/nas-shared');assert.equal(f.paused,false);assert.equal(f.path,'/fixture/data/connected');assert.equal(f.versioning.params.keep,'20');assert.equal(f.maxConflicts,-1);
await enrollLocal(client,nas,'/fixture/data/connected',register);assert.equal((await client.api('config/folders')).length,1);assert.equal(registered,2);
fs.mkdirSync('/fixture/data/connected',{recursive:true});fs.writeFileSync('/fixture/data/connected/keep.txt','retain on unbind');await detachLocal(client,nas,async(data)=>{assert.equal(data.action,'sync-unpair');assert.equal((await client.api('config/folders/nas-shared')).paused,true)});assert.equal((await client.api('config/folders')).length,0);
let bad=new LocalSyncthing(19384,'wrong-key');await assert.rejects(bad.api('system/status'));console.log('PASS: actual Syncthing enrollment, idempotence, version/conflict settings and invalid-key rejection');})().catch(e=>{console.error(e.message);process.exit(1)});
'''
  import sys
  sys.path.insert(0,str(root/'core/scripts'));from nas_sync import api
  nas=api(c,'system/status')['myID']
  result=run(['run','--rm','-i','--network','host','--read-only','--mount',f'type=bind,src={host}/data,dst=/fixture/data','--entrypoint','node',image,'-e',js],input=json.dumps({'key':key,'nas':nas}),timeout=60)
  assert (host/'data/connected/keep.txt').read_text()=='retain on unbind'
  assert (host/'data/nonempty/existing.txt').read_text()=='preserve user content'
  report={'state':'PASS','checks':['actual Syncthing accepts frontend-created device and paused folder','registration precedes sync activation','repeat enrollment preserves folder','20 versions and unlimited conflicts','incorrect API key rejected','unbind removes config and retains actual local file'],'limitations':['Node fetch against isolated real Syncthing; NAS registration stubbed to avoid adding a test device to production'],'work':str(host)}
 finally:
  if created:
   run(['stop','--time','30',name]);info=json.loads(run(['inspect',name]).stdout)[0]
   if info['State']['ExitCode']!=0:raise RuntimeError('Isolated test daemon did not stop cleanly')
   run(['rm',name])
 (host/'result.json').write_text(json.dumps(report,indent=2)+'\n');(root/'system/runtime/acceptance/connect-local.json').write_text(json.dumps(report,indent=2)+'\n');print(result.stdout.strip())
if __name__=='__main__':main()
