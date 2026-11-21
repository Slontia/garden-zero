#!/usr/bin/env python3
"""Real two-node offline/conflict/version recovery acceptance, isolated from production data."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import datetime,importlib.util,json,secrets,shutil,subprocess,time,urllib.request,uuid,xml.etree.ElementTree as ET
from pathlib import Path
from nasctl import cfg,DOCKER

def run(args,**kw):return subprocess.run(DOCKER+args,check=True,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**kw)
def main():
 c=cfg();stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6];work=Path(c['host'])/'state'/('sync-check-'+stamp);work.mkdir(mode=0o700);network='nas-sync-test-'+uuid.uuid4().hex[:8];created=[];checks=[]
 versions=json.loads((Path(c['host'])/'config/versions.json').read_text());image=versions['SYNCTHING_IMAGE']['image'];spec=importlib.util.spec_from_file_location('prepare',Path(__file__).resolve().parents[1]/'scripts/prepare-services.py');prep=importlib.util.module_from_spec(spec);spec.loader.exec_module(prep)
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));nodes=[]
 def wait(predicate,description,timeout=90):
  end=time.monotonic()+timeout
  while True:
   if predicate():return
   if time.monotonic()>end:raise RuntimeError('Timeout: '+description)
   time.sleep(.5)
 def api(n,path,data=None,method=None):
  script="import json,sys,urllib.request; d=json.load(sys.stdin); req=urllib.request.Request(d['url'],data=json.dumps(d['data']).encode() if d['data'] is not None else None,method=d['method'],headers={'X-API-Key':d['key'],'Content-Type':'application/json'}); r=urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=15); body=r.read(); print(body.decode() if body else 'null')"
  value=run(['exec','-i',network+'-api','python','-c',script],input=json.dumps({'url':n['url']+'/rest/'+path,'key':n['key'],'data':data,'method':method}))
  return json.loads(value.stdout)

 try:
  run(['network','create','--internal',network])
  helper=network+'-api';run(['create','--name',helper,'--network',network,'--read-only','--entrypoint','python','nas/web:1','-c','import signal,sys,time; signal.signal(signal.SIGTERM,lambda *args:sys.exit(0)); time.sleep(3600)']);created.append(helper);run(['start',helper])
  for label in ('a','b'):
   host=work/label;(host/'secrets').mkdir(parents=True);(host/'state').mkdir();(host/'secrets/admin-password.txt').write_text(secrets.token_urlsafe(24));prep.prepare_syncthing({'host':str(host)},versions)
   config=host/'state/syncthing';tree=ET.parse(config/'config.xml');options=tree.getroot().find('options')
   for key in ('globalAnnounceEnabled','localAnnounceEnabled','relaysEnabled','natEnabled'):options.find(key).text='false'
   for p in list(options.findall('listenAddress')):options.remove(p)
   ET.SubElement(options,'listenAddress').text='tcp://0.0.0.0:22000';tree.write(config/'config.xml')
   data=host/'data';data.mkdir();name=network+'-'+label
   run(['create','--name',name,'--network',network,'--read-only','--tmpfs','/tmp:size=16m','--tmpfs','/db:size=64m','--memory','256m','-e','STHOMEDIR=','--mount',f'type=bind,src={config},dst=/config','--mount',f'type=bind,src={data},dst=/var/syncthing/Shared','--entrypoint','/bin/syncthing',image,'serve','--config=/config','--data=/db','--no-browser','--no-restart','--no-upgrade']);created.append(name);run(['start',name]);info=json.loads(run(['inspect',name]).stdout)[0];n={'name':name,'data':data,'key':tree.findtext('gui/apikey'),'url':'http://'+name+':8384'};nodes.append(n)
   def ready():
    try:return api(n,'system/ping').get('ping')=='pong'
    except Exception:return False
   wait(ready,'test node starts');n['id']=api(n,'system/status')['myID']
  a,b=nodes
  for n,peer in ((a,b),(b,a)):
   dev=api(n,'config/defaults/device');dev.update(deviceID=peer['id'],name=peer['name'],addresses=['tcp://'+peer['name']+':22000'],introducer=False,autoAcceptFolders=False);api(n,'config/devices',dev,'POST')
   folder=api(n,'config/defaults/folder');folder.update(id='acceptance',path='/var/syncthing/Shared',type='sendreceive',ignorePerms=True,maxConflicts=-1,fsWatcherEnabled=False,rescanIntervalS=0,devices=[{'deviceID':n['id']},{'deviceID':peer['id']}]);folder['versioning']={'type':'simple','params':{'keep':'20','cleanoutDays':'0'},'cleanupIntervalS':3600,'fsPath':'','fsType':'basic'};api(n,'config/folders',folder,'POST')
  def scan(n):api(n,'db/scan?folder=acceptance',{},'POST')
  file='offline.txt';(a['data']/file).write_text('initial\n');scan(a);wait(lambda:(b['data']/file).exists() and (b['data']/file).read_text()=='initial\n','initial replication');checks.append('new file transfers between two real Syncthing nodes')
  for n,peer in ((a,b),(b,a)):api(n,'config/devices/'+peer['id'],{'paused':True},'PATCH')
  wait(lambda:not api(a,'system/connections')['connections'].get(b['id'],{}).get('connected',False),'nodes disconnected')
  before=[api(n,'db/file?folder=acceptance&file='+file)['local']['version'] for n in nodes]
  for n,text in ((a,'offline edit A\n'),(b,'offline edit B\n')):(n['data']/file).write_text(text);scan(n)
  wait(lambda:all(api(n,'db/file?folder=acceptance&file='+file)['local']['version']!=v for n,v in zip(nodes,before)),'both offline edits indexed');checks.append('disconnected nodes independently read/write and index local files')
  for n,peer in ((a,b),(b,a)):api(n,'config/devices/'+peer['id'],{'paused':False},'PATCH')
  def has_both():
   texts={(a['data']/file).read_text()} if (a['data']/file).exists() else set()
   texts.update(p.read_text() for p in a['data'].glob('*sync-conflict-*'))
   return {'offline edit A\n','offline edit B\n'}<=texts
  wait(has_both,'both contents preserved as conflict copies',120);checks.append('concurrent offline edits preserve both contents through conflict copies')
  # Allow peer to converge before testing a separate ordinary deletion.
  recover='recover.txt';(a['data']/recover).write_text('version to recover\n');scan(a);wait(lambda:(b['data']/recover).exists(),'recovery fixture replicated')
  (a['data']/recover).unlink();scan(a);wait(lambda:not (b['data']/recover).exists(),'deletion propagates')
  def archived():return [p for p in (b['data']/'.stversions').glob('recover~*.txt') if p.read_text()=='version to recover\n']
  wait(lambda:bool(archived()),'received deletion archived');checks.append('remote deletion propagates and receiver retains historical content')
  shutil.copyfile(archived()[0],b['data']/recover);scan(b);wait(lambda:(a['data']/recover).exists() and (a['data']/recover).read_text()=='version to recover\n','restored version re-synchronizes');checks.append('restored historical content synchronizes back to other node')
  report={'state':'PASS','checks':checks,'work':str(work),'limitations':['Two isolated Linux containers, not actual macOS client or company VPN','Fixtures and archived versions retained; no production folder or peer modified']}
 finally:
  for name in reversed(created):
   (work/(name+'-logs.txt')).write_text(run(['logs',name]).stdout)
   run(['stop','--time','30',name]);info=json.loads(run(['inspect',name]).stdout)[0]
   if info['State']['ExitCode'] not in ((0,143) if name.endswith('-api') else (0,)):raise RuntimeError('Test node did not stop cleanly; preserved '+name)
   run(['rm',name])
  run(['network','rm',network])
 (work/'result.json').write_text(json.dumps(report,indent=2)+'\n');(Path(c['root'])/'system/runtime/acceptance/full-sync.json').write_text(json.dumps(report,indent=2)+'\n');print('PASS:',len(checks),'real offline/conflict/recovery checks. '+str(work))
if __name__=='__main__':main()
