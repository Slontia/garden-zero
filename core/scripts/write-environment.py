#!/usr/bin/env python3
"""Resolve pinned remote images and local builds; never silently fall back to tags."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import argparse,json,os,subprocess
from pathlib import Path
from nasctl import cfg,DOCKER
LOCAL={'NAS_WEB_IMAGE','NAS_SERVICES_IMAGE'}
EXPECTED=LOCAL|{'IMMICH_SERVER_IMAGE','IMMICH_ML_IMAGE','POSTGRES_IMAGE','VALKEY_IMAGE','TAILSCALE_IMAGE','SYNCTHING_IMAGE','RESTIC_IMAGE','JOPLIN_IMAGE'}
def resolve(c):
 locks=json.loads((Path(c['root'])/'core/templates/versions.lock.json').read_text());versions={};missing=[]
 if set(locks)!=EXPECTED:raise RuntimeError('Incomplete or unexpected image lock manifest')
 for name,item in locks.items():
  ref=item['source'] if name in LOCAL else item['image']
  if name not in LOCAL and '@sha256:' not in ref:raise RuntimeError('Remote image is not digest pinned: '+name)
  try:info=json.loads(subprocess.check_output(DOCKER+['image','inspect',ref],stderr=subprocess.DEVNULL,text=True))[0]
  except subprocess.CalledProcessError:missing.append(name);continue
  if info['Architecture']!=item.get('architecture',info['Architecture']):raise RuntimeError('Image architecture not validated: '+name)
  if name not in LOCAL and info['Id']!=item.get('id'):raise RuntimeError('Pinned image content ID does not match the validated platform: '+name)
  image=info['Id'] if name in LOCAL else ref
  versions[name]={'source':item['source'],'image':image,'id':info['Id'],'architecture':info['Architecture']}
 return versions,missing

def generate(c,*,write=True):
 host=Path(c['host']);versions,missing=resolve(c)
 if missing:raise RuntimeError('Images not available locally; build/pull first: '+','.join(missing))
 remote=json.loads((host/'state/remote.json').read_text()) if (host/'state/remote.json').is_file() else {}
 existing={}
 if (host/'config/compose.env').exists():
  for line in (host/'config/compose.env').read_text().splitlines():
   if '=' in line:
    k,v=line.split('=',1);existing[k]=v
 values={'NAS_PUBLIC_ORIGIN':remote.get('meta',''),'NAS_HOST':c['host'],'NAS_DATA':c['data'],'NAS_HTTP_PROXY':existing.get('NAS_HTTP_PROXY',os.environ.get('http_proxy',os.environ.get('HTTP_PROXY',''))),'NAS_HTTPS_PROXY':existing.get('NAS_HTTPS_PROXY',os.environ.get('https_proxy',os.environ.get('HTTPS_PROXY',''))),**{k:v['image'] for k,v in versions.items()}}
 if any(any(ch in value for ch in '\r\n$') for value in values.values()):raise RuntimeError('Environment value requires explicit escaping; refusing write')
 if write:
  # Actual per-host resolution is private. Public version locks are never rewritten.
  for path,data in [(host/'config/compose.env',''.join(k+'='+v+'\n' for k,v in values.items())),(Path(c['host'])/'config/versions.json',json.dumps(versions,indent=2)+'\n')]:
   if path.is_symlink():raise RuntimeError('Environment/configuration target is a symlink')
   path.write_text(data)
 return {'images':len(versions),'state':'prepared' if write else 'validated','public_locks_modified':False}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');a=p.parse_args()
 try:print(json.dumps(generate(cfg(),write=not a.check)))
 except Exception as error:raise SystemExit('Environment preparation refused: '+str(error))
