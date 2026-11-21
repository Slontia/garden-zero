#!/usr/bin/env python3
"""Explicit migration from exclusive SMB writing to a full-copy shared space."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import argparse,datetime,json,shutil,subprocess
from pathlib import Path
from nasctl import cfg,DOCKER,lock,compose,audit,volume
from nas_sync import prepare,api

def main():
 p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');a=p.parse_args();c=cfg()
 if not a.apply:print(json.dumps({'mode':'read_only','space':'共享','source':'data/shared','sync':'full-copy sendreceive','history_versions':20,'conflicts':'unlimited copies','mac_pairing':'required'},ensure_ascii=False));return
 if subprocess.run(['systemctl','is-active','--quiet','nas-agent.service']).returncode==0:raise RuntimeError('先停止 nas-agent，迁移完成后再启动，避免旧授权留在内存')
 with lock():
  volume(c)
  state=Path(c['host'])/'state';stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S');saved=state/('full-sync-migration-'+stamp);saved.mkdir(mode=0o700)
  for source in [Path(c['host'])/'config/config.json',state/'syncthing/config.xml']:
   shutil.copyfile(source,saved/source.name)
  # Installed agent must be stopped/restarted by caller around migration so its in-memory grant cannot survive.
  from nas_writer import Writer
  Writer(c,audit).revoke('full-sync-migration')
  ts=DOCKER+['exec','nas-tailscale-1','tailscale'];routes=json.loads(subprocess.check_output(ts+['serve','status','--json'],text=True));(saved/'routes.json').write_text(json.dumps(routes,indent=2))
  old=routes.get('TCP',{}).get('1446')
  if old:
   if old!={'TCPForward':'127.0.0.1:1446','ProxyProtocol':2}:raise RuntimeError('Unexpected writer route; refusing replacement')
   subprocess.run(ts+['serve','--yes','--tcp=1446','off'],check=True,stdout=subprocess.DEVNULL)
  c['sync_folders_pending']=False
  c['file_access']={'mode':'full_sync','write_roots':['shared'],'writer_selection':'disabled','implemented':True}
  config=Path(c['host'])/'config/config.json';config.write_text(json.dumps(c,indent=2)+'\n')
  compose(c,'up','-d','--no-build','--wait','--wait-timeout','90','syncthing')
  summary=prepare(c)
  if routes.get('TCP',{}).get('22000') not in (None,{'TCPForward':'127.0.0.1:22000'}):raise RuntimeError('Sync route occupied; refusing replacement')
  if any(routes.get('AllowFunnel',{}).get(k) for k in routes.get('AllowFunnel',{}) if k.endswith(':22000')):raise RuntimeError('Sync must remain private')
  subprocess.run(ts+['serve','--bg','--yes','--tcp=22000','tcp://127.0.0.1:22000'],check=True,stdout=subprocess.DEVNULL)
  (saved/'result.json').write_text(json.dumps({'state':'prepared_pending_pairing','summary':summary},indent=2)+'\n');audit(c,'full-sync-migration',{'space':'shared','history':20,'pairing':'pending'})
 print('Full-copy shared space prepared; Mac pairing required. Evidence: '+str(saved))
if __name__=='__main__':main()
