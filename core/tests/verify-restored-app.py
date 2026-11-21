#!/usr/bin/env python3
"""Boot only an existing successful restore copy; never attach production storage."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import datetime,hashlib,json,os,subprocess,sys,tempfile,time,uuid
from pathlib import Path
from nasctl import CONFIG,db_signature
D=['/usr/bin/docker','--host','unix:///var/run/docker.sock']
def run(args,**kw):return subprocess.run([str(x) for x in args],check=True,**kw)
def out(args):return subprocess.check_output([str(x) for x in args],text=True).strip()
def main():
 if os.geteuid()!=0:raise RuntimeError('Requires root in Docker host mount namespace')
 if len(sys.argv)!=2:raise RuntimeError('Usage: verify-restored-app.py <successful-restore-directory>')
 c=json.loads(CONFIG.read_text());work=Path(sys.argv[1]).resolve();base=Path(c['backup_root']).resolve()
 if work.parent!=base or not work.name.startswith('restore-test-'):raise RuntimeError('Expected internal restore-test directory')
 prior=json.loads((work/'result.json').read_text())
 if prior['state']!='PASS':raise RuntimeError('Independent restore must pass first')
 image=work/'database.img';content=work/'content/data/immich';host=work/'configuration/host'
 if image.is_symlink() or image.resolve()==Path(c['image']).resolve() or not content.is_dir():raise RuntimeError('Unsafe restore paths')
 if out(['losetup','-j',image]):raise RuntimeError('Restore image already attached')
 if (work/'application-result.json').exists():raise RuntimeError('Application report exists; use a new independent restore')
 versions=json.loads((work/'configuration/system/config/versions.json').read_text())
 backup=Path(prior['backup']).resolve()
 if backup.parent!=base or not (backup/'COMPLETE').is_file():raise RuntimeError('Missing original completed backup')
 manifest=json.loads((backup/'manifest.json').read_text())
 suffix=uuid.uuid4().hex[:10];network='nas-restore-'+suffix
 point=Path(tempfile.mkdtemp(prefix=network+'-',dir='/run'));loop='';created=[];network_created=False
 checks=[];report={'state':'FAIL','restore_directory':str(work),'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'checks':checks,'limitations':['Same host only; not cross-host disaster recovery','Small fixture set; does not certify batch load or restored ML inference']}
 try:
  run(['e2fsck','-fn',image],stdout=subprocess.DEVNULL)
  loop=out(['losetup','--find','--show','--nooverlap',image]);run(['mount','-t','ext4','-o','nodev,nosuid,noexec',loop,point])
  run(D+['network','create','--internal','--label','nas.purpose=restore-app',network],stdout=subprocess.DEVNULL);network_created=True
  def create(kind,ref,args):
   name=network+'-'+kind
   run(D+['create','--name',name,'--network',network,'--network-alias',kind,'--label','nas.purpose=restore-app','--log-opt','max-size=5m','--log-opt','max-file=2']+args+[ref],stdout=subprocess.DEVNULL)
   created.append(name);run(D+['start',name],stdout=subprocess.DEVNULL);return name
  db=create('database',versions['POSTGRES_IMAGE']['image'],['--memory','768m','--shm-size','128m','--mount',f'type=bind,source={point}/postgres,target=/var/lib/postgresql/data','-e','POSTGRES_HOST_AUTH_METHOD=trust'])
  for _ in range(90):
   if subprocess.run(D+['exec',db,'pg_isready','-U','postgres','-d','immich'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:break
   time.sleep(1)
  else:raise RuntimeError('Restored DB readiness timeout')
  if db_signature(db)!=manifest['tables']:raise RuntimeError('Restore table counts changed before application boot')
  checks.append('restored database table counts match backup before application boot')
  query='SELECT COALESCE(json_agg(t),\'[]\'::json)::text FROM (SELECT id, "originalPath" AS path FROM asset WHERE "deletedAt" IS NULL ORDER BY id LIMIT 10) t'
  assets=json.loads(out(D+['exec',db,'psql','-U','postgres','-d','immich','-Atc',query]))
  if not assets:raise RuntimeError('No live assets available for restored API verification')
  samples=[]
  for a in assets:
   rel=Path(a['path']).relative_to('/data');file=(content/rel).resolve()
   if not file.is_relative_to(content.resolve()) or not file.is_file():raise RuntimeError('Asset path missing or outside restored content')
   samples.append({'id':a['id'],'sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
  create('cache',versions['VALKEY_IMAGE']['image'],['--memory','128m'])
  config=work/'application-immich.json';config.write_text(json.dumps({'machineLearning':{'enabled':False}}))
  app=create('app',versions['IMMICH_SERVER_IMAGE']['image'],['--memory','1536m','--cpus','1','--mount',f'type=bind,source={content},target=/data','--mount',f'type=bind,source={config},target=/config/immich.json,readonly','-e','DB_HOSTNAME=database','-e','DB_USERNAME=postgres','-e','DB_PASSWORD=unused-isolated-trust','-e','DB_DATABASE_NAME=immich','-e','REDIS_HOSTNAME=cache','-e','IMMICH_CONFIG_FILE=/config/immich.json'])
  for _ in range(150):
   state=json.loads(out(D+['inspect',app]))[0]['State']
   if state['Status']!='running':raise RuntimeError('Restored application exited')
   if subprocess.run(D+['exec',app,'node','-e',"fetch('http://localhost:2283/api/server/ping').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:break
   time.sleep(2)
  else:raise RuntimeError('Restored API readiness timeout')
  checks.append('isolated Immich and cache start without production ports or network membership')
  for name in created:
   info=json.loads(out(D+['inspect',name]))[0]
   if info['HostConfig'].get('PortBindings') or set(info['NetworkSettings']['Networks'])!={network}:raise RuntimeError('Unexpected isolation configuration')
  password=(host/'secrets/admin-password.txt').read_text().strip()
  # Credentials travel over stdin, not process arguments or log output.
  payload={'password':password,'samples':samples}
  js="""const fs=require('fs'),crypto=require('crypto');(async()=>{const p=JSON.parse(fs.readFileSync(0,'utf8')),base='http://localhost:2283/api';let r=await fetch(base+'/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:'admin@nas.local',password:p.password})});if(!r.ok)throw Error('restored login rejected');const login=await r.json(),headers={Authorization:'Bearer '+login.accessToken};for(const s of p.samples){r=await fetch(base+'/assets/'+s.id,{headers});if(!r.ok||(await r.json()).id!==s.id)throw Error('asset metadata mismatch');r=await fetch(base+'/assets/'+s.id+'/original',{headers});if(!r.ok||crypto.createHash('sha256').update(Buffer.from(await r.arrayBuffer())).digest('hex')!==s.sha256)throw Error('restored original checksum mismatch')}r=await fetch(base+'/auth/logout',{method:'POST',headers});if(!r.ok)throw Error('logout failed');console.log(JSON.stringify({authenticated:true,assets_verified:p.samples.length,logout:true}))})().catch(e=>{console.error(e.message);process.exit(1)})"""
  result=run(D+['exec','-i',app,'node','-e',js],input=json.dumps(payload),text=True,capture_output=True)
  report['api']=json.loads(result.stdout);report['samples']=samples
  checks.extend(['login with credentials recovered from backup','asset metadata and original SHA-256 verified through restored API','restored session logout'])
 except Exception as e:
  report['error']=type(e).__name__+': '+str(e)[:700]
  raise
 finally:
  # Every resource is unique to this test. A bad DB stop retains its mount for inspection.
  try:
   for name in reversed(created):
    run(D+['stop','--timeout','60',name],stdout=subprocess.DEVNULL)
    info=json.loads(out(D+['inspect',name]))[0]
    if info['State']['ExitCode']==137 or info['State'].get('OOMKilled'):raise RuntimeError('Unclean test stop; restore mount retained')
    if name.endswith('-database') and info['State']['ExitCode']!=0:raise RuntimeError('Unclean test database stop; restore mount retained')
    run(D+['rm',name],stdout=subprocess.DEVNULL)
   if network_created:run(D+['network','rm',network],stdout=subprocess.DEVNULL)
   if subprocess.run(['mountpoint','-q',point]).returncode==0:run(['umount',point])
   if loop:run(['losetup','-d',loop])
   point.rmdir();run(['e2fsck','-fn',image],stdout=subprocess.DEVNULL)
   checks.append('test containers removed, clean database stop, loop detached and read-only fsck passed')
   if 'error' not in report:report['state']='PASS'
  except Exception as e:
   report['state']='FAIL';report['cleanup_error']=str(e);raise
  finally:(work/'application-result.json').write_text(json.dumps(report,indent=2))
 print('PASS: restored application login, original downloads and clean shutdown. '+str(work/'application-result.json'))
if __name__=='__main__':main()
