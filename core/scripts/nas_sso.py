"""Prepare private identity settings and cached Tailscale TLS certificates; never print secrets."""
import sys
sys.dont_write_bytecode=True
import hashlib,json,os,secrets,subprocess,time
from pathlib import Path

def secret(path,length=32):
 path=Path(path)
 if path.is_symlink():raise RuntimeError('Linked identity secret refused')
 if not path.exists():
  fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'w') as stream:stream.write(secrets.token_hex(length)+'\n')
 return path.read_text().strip()

def prepare(c):
 host=Path(c['host']);remote=json.loads((host/'state/remote.json').read_text());origin=remote.get('meta','')
 if not origin.startswith('https://') or not remote.get('name'):raise RuntimeError('Private HTTPS authorization must be completed first')
 secret(host/'secrets/platform_auth-db-password');key=secret(host/'secrets/authentik-secret',48);password=secret(host/'secrets/authentik-admin-password');token=secret(host/'secrets/authentik-bootstrap-token')
 directory=host/'state/authentik';directory.mkdir(exist_ok=True)
 os.chown(directory,1000,1000)
 (host/'config/identity.Caddyfile').write_bytes((Path(c['root'])/'core/identity/Caddyfile').read_bytes())
 if directory.is_symlink():raise RuntimeError('Linked identity state refused')
 values={'AUTHENTIK_POSTGRESQL__HOST':'database','AUTHENTIK_POSTGRESQL__NAME':'platform_auth','AUTHENTIK_POSTGRESQL__USER':'platform_auth','AUTHENTIK_POSTGRESQL__PASSWORD':secret(host/'secrets/platform_auth-db-password'),'AUTHENTIK_POSTGRESQL__SSLMODE':'disable','AUTHENTIK_SECRET_KEY':key,'AUTHENTIK_BOOTSTRAP_PASSWORD':password,'AUTHENTIK_BOOTSTRAP_TOKEN':token,'AUTHENTIK_BOOTSTRAP_EMAIL':'recovery@nas.local','AUTHENTIK_WEB__PATH':'/auth/','AUTHENTIK_WEB__BASE_URL':origin,'AUTHENTIK_WEB__WORKERS':'2','AUTHENTIK_WEB__THREADS':'2','AUTHENTIK_WORKER__PROCESSES':'1','AUTHENTIK_WORKER__THREADS':'2','AUTHENTIK_ERROR_REPORTING__ENABLED':'false','AUTHENTIK_DISABLE_UPDATE_CHECK':'true','AUTHENTIK_REPUTATION__EXPIRY':'300'}
 path=host/'config/authentik.env'
 if path.is_symlink():raise RuntimeError('Linked identity settings refused')
 path.write_text(''.join(k+'='+v+'\n' for k,v in values.items()));path.chmod(0o600)
 # Only infrastructure preparation is enabled here; app login switches after end-to-end checks.
 c['sso']={**c.get('sso',{}),'prepared':True,'origin':origin,'path':'/auth/'}
 (host/'config/config.json').write_text(json.dumps(c,indent=2)+'\n')
 return renew_certificate(c,reload=False)

def wait_network_identity(name,timeout=60,interval=1):
 """A running daemon/socket is insufficient: cert requires its initialized netmap."""
 from nasctl import DOCKER
 deadline=time.monotonic()+timeout
 while time.monotonic()<deadline:
  try:
   result=subprocess.run(DOCKER+['exec','nas-tailscale-1','tailscale','status','--json'],capture_output=True,text=True,timeout=5)
   status=json.loads(result.stdout) if result.returncode==0 else {}
  except (OSError,subprocess.TimeoutExpired,ValueError):status={}
  backend=status.get('BackendState')
  if backend in ('NeedsLogin','NeedsMachineAuth'):raise RuntimeError('Tailscale requires device authorization before identity certificate renewal')
  if backend=='Running' and (status.get('Self') or {}).get('DNSName','').rstrip('.').lower()==name.rstrip('.').lower():return
  time.sleep(interval)
 raise RuntimeError('Tailscale network identity did not become ready before identity certificate renewal')

def renew_certificate(c,reload=True):
 from nasctl import DOCKER
 host=Path(c['host']);remote=json.loads((host/'state/remote.json').read_text());name=remote['name'];state=host/'state/tailscale';directory=host/'secrets/identity-tls';directory.mkdir(mode=0o700,exist_ok=True)
 if directory.is_symlink():raise RuntimeError('Linked certificate directory refused')
 wait_network_identity(name)
 subprocess.run(DOCKER+['exec','nas-tailscale-1','tailscale','cert','--cert-file=/var/lib/tailscale/identity.crt','--key-file=/var/lib/tailscale/identity.key',name],check=True,stdout=subprocess.DEVNULL)
 changed=False
 for source,target in [('identity.crt','tls.crt'),('identity.key','tls.key')]:
  data=(state/source).read_bytes();path=directory/target
  if path.is_symlink():raise RuntimeError('Linked certificate file refused')
  if not path.exists() or path.read_bytes()!=data:
   temporary=path.with_suffix('.new');temporary.write_bytes(data);temporary.chmod(0o600);temporary.replace(path);changed=True
 digest=hashlib.sha256((directory/'tls.crt').read_bytes()).hexdigest();record=host/'state/identity-certificate.json'
 previous=json.loads(record.read_text()) if record.exists() else {}
 if reload and (changed or previous.get('sha256')!=digest):
  subprocess.run(DOCKER+['exec','nas-identity-gateway-1','caddy','reload','--force','--config','/etc/caddy/Caddyfile'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 (host/'state/identity-certificate.json').write_text(json.dumps({'state':'ready','checked_at':int(time.time()),'domain':name,'sha256':digest})+'\n')
 return {'state':'prepared','domain':name,'certificate_changed':changed}

if __name__=='__main__':
 from nasctl import cfg,lock
 with lock():print(json.dumps(prepare(cfg())))
