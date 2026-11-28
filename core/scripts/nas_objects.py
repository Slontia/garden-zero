"""Provider-neutral bounded object transport over the configured rclone account.

Only a backup worker is created/stopped. Object names are sharded hashes, avoiding
an ever-growing cloud directory on the normal lookup/write path.
"""
import hashlib,json,re,subprocess,uuid
from pathlib import Path
from nas_stream import Missing,IntegrityError,path
from nas_backup_control import check,run

DOCKER=['/usr/bin/docker','--host','unix:///var/run/docker.sock']
ROOT='native-v1'

def object_path(name,root=ROOT):
 path(name)
 if not re.fullmatch(r'native-(?:v1|test-[a-f0-9]{32})',root):raise IntegrityError('Invalid owned cloud namespace')
 if name=='repository.json':return root+'/repository.json'
 value=hashlib.sha256(name.encode()).hexdigest()
 return root+'/objects/'+'/'.join(value[i:i+2] for i in range(0,6,2))+'/'+value

class RcloneStore:
 def __init__(self,c,account,*,bandwidth='4M',namespace=ROOT):
  ident=account['id']
  if not re.fullmatch('[a-z][a-z0-9-]{0,79}',ident):raise RuntimeError('Invalid cloud transport identity')
  if not re.fullmatch(r'[1-9][0-9]{0,3}[kKmM]',bandwidth):raise RuntimeError('Invalid transfer bandwidth budget')
  object_path('repository.json',namespace);self.namespace=namespace
  self.c=c;self.ident=ident;self.bandwidth=bandwidth;self.worker='nas-native-'+uuid.uuid4().hex;self.active=False
 def __enter__(self):
  image=json.loads((Path(self.c['host'])/'config/versions.json').read_text())['CLOUD_BACKUP_IMAGE']['image']
  config=Path(self.c['host'])/'secrets/cloud-rclone.conf'
  if config.is_symlink() or not config.is_file():raise RuntimeError('Missing or linked cloud credentials')
  cmd=DOCKER+['run','-d','--name',self.worker,'--label','nas.purpose=native-cloud-backup','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges:true','--network',self.c['project']+'_default','--memory','256m','--memory-swap','256m','--cpus','0.5','--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=32m','--mount',f'type=bind,source={config},target=/credentials/rclone.conf,readonly','-e','RCLONE_CONFIG=/credentials/rclone.conf','-e','HOME=/tmp']
  for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):cmd+=['-e',key+'=']
  cmd+=['-e','NO_PROXY=*','--entrypoint','/bin/sh',image,'-c','exec sleep 2147483647']
  try:run(cmd,capture_output=True,check=True)
  except BaseException:
   # Cancellation can arrive after Docker created the worker but before run()
   # returns. Inspect only our unpredictable name; never clean other tasks.
   result=subprocess.run(DOCKER+['inspect','--format','{{index .Config.Labels "nas.purpose"}}',self.worker],capture_output=True,text=True,timeout=15)
   if result.returncode==0:
    if result.stdout.strip()!='native-cloud-backup':raise RuntimeError('Interrupted worker ownership mismatch')
    subprocess.run(DOCKER+['rm','-f',self.worker],capture_output=True,timeout=30,check=True)
   raise
  self.active=True;return self
 def __exit__(self,*error):
  if self.active:
   # Private unique worker name plus ownership label, never a business container.
   label=subprocess.check_output(DOCKER+['inspect','--format','{{index .Config.Labels "nas.purpose"}}',self.worker],text=True).strip()
   if label!='native-cloud-backup':raise RuntimeError('Backup worker ownership changed')
   subprocess.run(DOCKER+['rm','-f',self.worker],capture_output=True,timeout=30,check=True);self.active=False
 def command(self,args,*,data=None,limit=65536,missing=False):
  if not self.active:raise RuntimeError('Object transport is not active')
  check()
  cmd=DOCKER+['exec']+(['-i'] if data is not None else [])+[self.worker,'rclone','--config','/credentials/rclone.conf','--bwlimit',self.bandwidth,'--transfers','1','--checkers','1','--retries','1','--low-level-retries','1','--contimeout','10s','--timeout','60s',*args]
  # cat is constrained at the producer with --head; normal outputs are small.
  result=run(cmd,input=data,capture_output=True,timeout=600)
  if result.returncode:
   if missing and result.returncode in (3,4):raise Missing('Cloud object absent')
   raise RuntimeError('Native cloud object transfer failed; previous committed backups are retained')
  if len(result.stdout)>limit:raise IntegrityError('Cloud response exceeded its read budget')
  return result.stdout
 def remote(self,name):return self.ident+':'+object_path(name,self.namespace)
 def put(self,name,data):
  if len(data)>16*1024**2+32:raise IntegrityError('Cloud object exceeds buffer budget')
  self.command(['rcat',self.remote(name),'--size',str(len(data))],data=data)
 def get(self,name,limit):
  if not 1<=limit<=16*1024**2+32:raise IntegrityError('Invalid cloud read budget')
  return self.command(['cat',self.remote(name),'--head',str(limit+1)],limit=limit,missing=True)
 def empty(self):
  # Conservatively accept only an absent managed namespace. A stat response is
  # bounded even if someone placed millions of files in an unknown directory.
  try:self.command(['lsjson',self.ident+':'+self.namespace,'--stat'],limit=65536,missing=True)
  except Missing:return True
  return False
