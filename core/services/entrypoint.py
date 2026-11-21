import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import os,subprocess,shlex,re,sys
from pathlib import Path
mode=os.environ.get('NAS_MODE')
if len(sys.argv)>1 and sys.argv[1]=='git-command':
 try:
  words=shlex.split(os.environ.get('SSH_ORIGINAL_COMMAND',''))
  if len(words)!=2 or words[0] not in ('git-upload-pack','git-receive-pack','git-upload-archive'):raise ValueError()
  name=words[1].removeprefix('/repos/').removeprefix('/')
  if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*\.git',name):raise ValueError()
  path=Path('/repos')/name
  if path.is_symlink() or not path.is_dir():raise ValueError()
  os.execv('/usr/bin/'+words[0],[words[0],str(path)])
 except Exception:
  print('Only authorized Git repository operations are allowed.',file=sys.stderr);sys.exit(1)
if mode=='git':
 Path('/run/sshd').mkdir(parents=True,exist_ok=True)
 state=Path('/state');state.mkdir(exist_ok=True)
 if not (state/'ssh_host_ed25519_key').exists():subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(state/'ssh_host_ed25519_key')],check=True)
 # NTFS state permissions cannot satisfy sshd private-key checks: copy to private tmpfs.
 key=Path('/run/ssh_host_ed25519_key');key.write_bytes((state/'ssh_host_ed25519_key').read_bytes());key.chmod(0o600)
 keys=Path('/credentials/authorized_keys')
 Path('/run/authorized_keys').write_text(keys.read_text() if keys.exists() else '')
 Path('/run/authorized_keys').chmod(0o644)
 config='''Port 22
ListenAddress 0.0.0.0
HostKey /run/ssh_host_ed25519_key
PidFile /run/sshd.pid
AuthorizedKeysFile /run/authorized_keys
StrictModes yes
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
UsePAM no
AllowUsers nas
AllowTcpForwarding no
AllowAgentForwarding no
X11Forwarding no
PermitTunnel no
PermitTTY no
ForceCommand /usr/local/bin/python /app/entrypoint.py git-command
LogLevel VERBOSE
'''
 Path('/run/sshd_config').write_text(config)
 # Unlock account with an unguessable password while password authentication stays disabled.
 import secrets
 subprocess.run(['chpasswd'],input='nas:'+secrets.token_hex(48)+'\n',text=True,check=True)
 os.execv('/usr/sbin/sshd',['/usr/sbin/sshd','-D','-e','-f','/run/sshd_config'])
elif mode=='smb':
 Path('/run/samba/private').mkdir(parents=True,exist_ok=True)
 password=Path('/credentials/smb-password').read_text().strip()
 config='''[global]
server role = standalone server
workgroup = WORKGROUP
security = user
map to guest = Never
server min protocol = SMB2
smb ports = 445
log file = /dev/stdout
logging = file
private dir = /run/samba/private
state directory = /run/samba
cache directory = /run/samba
lock directory = /run/samba
pid directory = /run/samba
unix extensions = no
[nas]
path = /data
read only = yes
guest ok = no
valid users = nas
follow symlinks = no
wide links = no
'''
 Path('/run/smb.conf').write_text(config)
 subprocess.run(['smbpasswd','-c','/run/smb.conf','-s','-a','nas'],input=password+'\n'+password+'\n',text=True,check=True,stdout=subprocess.DEVNULL)
 os.execv('/usr/sbin/smbd',['smbd','--foreground','--no-process-group','--configfile=/run/smb.conf','--debug-stdout'])
else:raise SystemExit('Unknown NAS service mode')
