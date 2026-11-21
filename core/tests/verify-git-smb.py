import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import json,os,re,shlex,subprocess,tempfile
from pathlib import Path
D=['/usr/bin/docker','--host','unix:///var/run/docker.sock'];h=Path('/mnt/c/nas/system');evidence=Path('/mnt/d/nas/system/runtime/acceptance');results=[]
def run(args,**kw):return subprocess.run(args,check=True,**kw)
keys=h/'state/git/authorized_keys';original=keys.read_text();windows=Path('/mnt/c/Users/Lenovo/.ssh/id_rsa.pub')
public=windows.read_text().strip() if windows.exists() else ''
if public and not re.fullmatch(r'(ssh-rsa|ssh-ed25519|ecdsa-sha2-nistp\d+) [A-Za-z0-9+/=]+(?: [^\r\n]*)?',public):raise RuntimeError('Unexpected public key syntax')
with tempfile.TemporaryDirectory(prefix='nas-git-acceptance-',dir='/run') as tmp:
 t=Path(tmp);key=t/'key'
 run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)])
 keys.write_text((original.strip()+'\n' if original.strip() else '')+(public+'\n' if public and public not in original else '')+key.with_suffix('.pub').read_text())
 try:
  run(D+['restart','nas-git-1'],stdout=subprocess.DEVNULL)
  import time;time.sleep(2)
  hostkey=(h/'state/git/ssh_host_ed25519_key.pub').read_text().split()[:2]
  (t/'known_hosts').write_text('[127.0.0.1]:2222 '+' '.join(hostkey)+'\n')
  ssh=['ssh','-i',str(key),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(t/'known_hosts'),'-p','2222']
  env=os.environ.copy();env['GIT_SSH_COMMAND']=shlex.join(ssh)
  run(D+['exec','-u','nas','nas-git-1','git','-c','core.filemode=false','init','--bare','/repos/acceptance.git'],stdout=subprocess.DEVNULL)
  local=t/'work';run(['git','init','-q',str(local)])
  (local/'README.md').write_text('NAS SSH acceptance\n')
  run(['git','-C',str(local),'add','README.md']);run(['git','-C',str(local),'-c','user.name=NAS Acceptance','-c','user.email=acceptance@nas.local','commit','-qm','acceptance'])
  url='ssh://nas@127.0.0.1:2222/acceptance.git'
  run(['git','-C',str(local),'push',url,'HEAD:main'],env=env,stdout=subprocess.DEVNULL)
  clone=t/'clone';run(['git','clone','-q','-b','main',url,str(clone)],env=env)
  assert (clone/'README.md').read_text()==(local/'README.md').read_text()
  run(['git','-C',str(clone),'fsck','--full'],stdout=subprocess.DEVNULL)
  run(D+['exec','-u','nas','nas-git-1','git','--git-dir=/repos/acceptance.git','gc'],stdout=subprocess.DEVNULL)
  run(D+['exec','-u','nas','nas-git-1','git','--git-dir=/repos/acceptance.git','fsck','--full'],stdout=subprocess.DEVNULL)
  denial=subprocess.run(ssh+['nas@127.0.0.1','id'],capture_output=True,text=True)
  assert denial.returncode!=0 and 'Only authorized Git' in denial.stderr
  results.append({'test':'Git SSH push/clone/content/fsck/gc and arbitrary shell rejection','result':'PASS'})
 finally:
  keys.write_text(original+(public+'\n' if public and public not in original else ''))
  run(D+['restart','nas-git-1'],stdout=subprocess.DEVNULL)
# Test the Samba protocol with credentials fed over stdin, never CLI arguments.
password=(h/'secrets/smb-password').read_text().strip()
run(D+['exec','-i','nas-smb-1','sh','-c','umask 077; cat > /run/acceptance.auth'],input='username=nas\npassword='+password+'\n',text=True)
try:
 read=subprocess.run(D+['exec','nas-smb-1','smbclient','//127.0.0.1/nas','-A','/run/acceptance.auth','-c','get shared/acceptance.txt /tmp/nas-readback'],capture_output=True,text=True)
 assert read.returncode==0,read.stderr
 content=subprocess.check_output(D+['exec','nas-smb-1','cat','/tmp/nas-readback'])
 assert content==Path('/mnt/d/nas/data/shared/acceptance.txt').read_bytes()
 write=subprocess.run(D+['exec','nas-smb-1','smbclient','//127.0.0.1/nas','-A','/run/acceptance.auth','-c','put /etc/hostname shared/forbidden-smb-write'],capture_output=True,text=True)
 assert write.returncode!=0 and 'ACCESS_DENIED' in write.stdout+write.stderr
 assert not Path('/mnt/d/nas/data/shared/forbidden-smb-write').exists()
 results.append({'test':'SMB authenticated download and upload denied','result':'PASS'})
finally:run(D+['exec','nas-smb-1','python','-c',"from pathlib import Path;Path('/run/acceptance.auth').unlink(missing_ok=True);Path('/tmp/nas-readback').unlink(missing_ok=True)"])
(evidence/'git-smb.json').write_text(json.dumps(results,indent=2))
print('PASS: Git SSH and SMB read-only acceptance. Windows public key imported; temporary test key revoked.')
