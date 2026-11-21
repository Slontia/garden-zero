#!/usr/bin/env python3
"""Shared Docker/WSL infrastructure: persist proxy setup, recompute NAT gateway on start."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import argparse,ipaddress,json,os,socket,subprocess,tempfile
from pathlib import Path
SETTINGS=Path('/etc/docker/wsl-proxy.json');ENV=Path('/run/docker-wsl-proxy.env')
HELPER=Path('/usr/local/lib/docker-wsl-proxy.py');DROPIN=Path('/etc/systemd/system/docker.service.d/20-wsl-proxy.conf')
UNIT='''[Unit]
After=network-online.target
Wants=network-online.target
[Service]
EnvironmentFile=-/run/docker-wsl-proxy.env
ExecStartPre=/usr/bin/python3 /usr/local/lib/docker-wsl-proxy.py --write-runtime
'''
def detect(port):
 if not 1<=port<=65535:raise RuntimeError('Invalid proxy port')
 if 'microsoft' not in Path('/proc/sys/kernel/osrelease').read_text().lower():raise RuntimeError('Only the current WSL2 NAT host is supported')
 routes=json.loads(subprocess.check_output(['ip','-j','-4','route','show','default'],text=True));routes.sort(key=lambda r:r.get('metric',0))
 candidates=[r['gateway'] for r in routes if r.get('gateway') and r.get('dev')=='eth0']
 if not candidates:raise RuntimeError('No WSL NAT default gateway available')
 gateway=str(ipaddress.IPv4Address(candidates[0]))
 if not ipaddress.ip_address(gateway).is_private:raise RuntimeError('WSL gateway is not private')
 try:
  with socket.create_connection((gateway,port),timeout=2):pass
  reachable=True
 except OSError:reachable=False
 return {'gateway':gateway,'port':port,'reachable':reachable}

def render(info):
 proxy='http://'+info['gateway']+':'+str(info['port'])
 return 'HTTP_PROXY='+proxy+'\nHTTPS_PROXY='+proxy+'\nNO_PROXY=localhost,127.0.0.1,::1\n'
def atomic(path,data,mode):
 path=Path(path)
 if path.is_symlink():raise RuntimeError('Proxy configuration target is a symlink')
 path.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
 try:
  os.fchmod(fd,mode)
  with os.fdopen(fd,'w') as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.replace(tmp,path)
 finally:
  if Path(tmp).exists():Path(tmp).unlink()

def main():
 p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group();g.add_argument('--install',action='store_true');g.add_argument('--write-runtime',action='store_true');p.add_argument('--port',type=int,default=17890);a=p.parse_args()
 setting=json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {'port':a.port}
 info=detect(setting['port'])
 if not a.install and not a.write_runtime:print(json.dumps({'mode':'read_only',**info}));return
 if os.geteuid()!=0:raise RuntimeError('Requires root')
 if a.write_runtime:
  atomic(ENV,render(info),0o600);print('Docker WSL proxy refreshed; reachable='+str(info['reachable']));return
 if not info['reachable']:raise RuntimeError('Windows proxy is not reachable; existing Docker configuration unchanged')
 daemon=Path('/etc/docker/daemon.json')
 if daemon.exists() and json.loads(daemon.read_text()).get('proxies'):raise RuntimeError('Existing daemon proxy settings take precedence; inspect instead of overriding')
 files={HELPER:(Path(__file__).read_text(),0o644),DROPIN:(UNIT,0o644),SETTINGS:(json.dumps({'schema':1,'port':info['port']},indent=2)+'\n',0o600)}
 for path,(data,mode) in files.items():
  if path.is_symlink() or (path.exists() and path.read_text()!=data):raise RuntimeError('Existing shared proxy configuration differs: '+str(path))
 for path,(data,mode) in files.items():
  if not path.exists():atomic(path,data,mode)
 subprocess.run(['systemctl','daemon-reload'],check=True)
 print('Persistent WSL Docker proxy installed; controlled Docker restart still required. No storage paths changed.')
if __name__=='__main__':
 try:main()
 except Exception as error:raise SystemExit('Docker proxy setup refused: '+str(error))
