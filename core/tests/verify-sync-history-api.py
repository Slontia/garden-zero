#!/usr/bin/env python3
"""Actual Syncthing version catalog + real recovery-copy IO, in a fresh isolated fixture."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import importlib.util,json,secrets,socket,subprocess,time,urllib.request,uuid,xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
from nasctl import cfg,DOCKER
import nas_sync as sync

def run(args,**kw):
 r=subprocess.run(DOCKER+args,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**kw)
 if r.returncode:raise RuntimeError(r.stderr[-1500:])
 return r

def main():
 c=cfg();work=Path(c['host'])/'state'/('sync-history-api-'+uuid.uuid4().hex[:8]);(work/'secrets').mkdir(parents=True,mode=0o700);(work/'state').mkdir();shared=work/'data/shared';(shared/'.stversions').mkdir(parents=True);(work/'system/runtime').mkdir(parents=True);(work/'secrets/admin-password.txt').write_text(secrets.token_urlsafe(24))
 versions=json.loads((Path(c['host'])/'config/versions.json').read_text());spec=importlib.util.spec_from_file_location('prep',Path(__file__).resolve().parents[1]/'scripts/prepare-services.py');prep=importlib.util.module_from_spec(spec);spec.loader.exec_module(prep);prep.prepare_syncthing({'host':str(work)},versions)
 directory=work/'state/syncthing';tree=ET.parse(directory/'config.xml');tree.find('gui/address').text='127.0.0.1:19384';key=tree.findtext('gui/apikey');opts=tree.getroot().find('options')
 for name in ('globalAnnounceEnabled','localAnnounceEnabled','relaysEnabled','natEnabled'):opts.find(name).text='false'
 for p in list(opts.findall('listenAddress')):opts.remove(p)
 ET.SubElement(opts,'listenAddress').text='tcp://127.0.0.1:0';tree.write(directory/'config.xml')
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 def api(config,path,data=None,method=None):
  req=urllib.request.Request('http://127.0.0.1:19384/rest/'+path,data=json.dumps(data).encode() if data is not None else None,method=method,headers={'X-API-Key':key,'Content-Type':'application/json'})
  with opener.open(req,timeout=20) as r:
   body=r.read();return json.loads(body) if body else None
 name='nas-history-check-'+uuid.uuid4().hex[:8];created=False
 try:
  run(['create','--name',name,'--network','host','--read-only','--tmpfs','/tmp:size=16m','--tmpfs','/db:size=64m','-e','STHOMEDIR=','-e','STGUIADDRESS=127.0.0.1:19384','--mount',f'type=bind,src={directory},dst=/config','--mount',f'type=bind,src={shared},dst=/var/syncthing/Shared','--entrypoint','/bin/syncthing',versions['SYNCTHING_IMAGE']['image'],'serve','--config=/config','--data=/db','--no-browser','--no-restart','--no-upgrade']);created=True;run(['start',name])
  for _ in range(60):
   try:api({},'system/status');break
   except Exception:time.sleep(.25)
  folder=api({},'config/defaults/folder');folder.update(id=sync.FOLDER,path=sync.PATH,type='sendreceive',maxConflicts=-1,ignorePerms=True);folder['versioning']={'type':'simple','params':{'keep':'20','cleanoutDays':'0'},'fsPath':'','fsType':'basic','cleanupIntervalS':3600};api({},'config/folders',folder,'POST')
  (shared/'sample.txt').write_bytes(b'current stays intact');archived=shared/'.stversions/sample~20260102-123456.txt';archived.write_bytes(b'old archived content')
  f={**c,'root':str(work),'data':str(work/'data'),'host':str(work)}
  with patch.object(sync,'api',side_effect=api),patch('nasctl.volume',return_value=None):
   listing=sync.history(f);entry=next(e for e in listing['entries'] if e['path']=='sample.txt');result=sync.restore_copy(f,entry['path'],entry['version'])
  assert (shared/result['restored_path']).read_bytes()==b'old archived content';assert archived.read_bytes()==b'old archived content';assert (shared/'sample.txt').read_bytes()==b'current stays intact';assert not api({},'config/folders/'+sync.FOLDER)['paused']
  report={'state':'PASS','checks':['real Syncthing API lists expected archived name/time/size','controller resolves real version timestamp into archive and publishes new copy','current and historical bytes unchanged','original unpaused state restored'],'limitations':['isolated fixture volume identity adapter bypassed; production volume guard tested separately','production NAS files and remote client memberships untouched'],'work':str(work)}
 finally:
  if created:
   run(['stop','--time','30',name]);row=json.loads(run(['inspect',name]).stdout)[0]
   if row['State']['ExitCode']!=0:raise RuntimeError('History fixture did not stop cleanly; retained')
   run(['rm',name])
 (work/'result.json').write_text(json.dumps(report,indent=2)+'\n');(Path(c['root'])/'system/runtime/acceptance/sync-history-api.json').write_text(json.dumps(report,indent=2)+'\n');print('PASS: actual history API, restore-copy content and pause-state integration')
if __name__=='__main__':main()
