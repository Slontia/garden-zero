#!/usr/bin/env python3
"""Ephemeral SMB backend: only /write is writable; reachable on host loopback only."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import os,subprocess
from pathlib import Path
Path('/run/samba/private').mkdir(parents=True,exist_ok=True)
config='''[global]
server role = standalone server
security = user
map to guest = Never
server min protocol = SMB2
interfaces = 127.0.0.1
bind interfaces only = yes
smb ports = 1447
server multi channel support = no
smb2 leases = no
unix extensions = no
load printers = no
disable spoolss = yes
log file = /dev/stdout
private dir = /run/samba/private
state directory = /run/samba
cache directory = /run/samba
lock directory = /run/samba
pid directory = /run/samba
[shared]
path = /write
read only = no
guest ok = no
valid users = nas
follow symlinks = no
wide links = no
oplocks = no
level2 oplocks = no
durable handles = no
strict sync = yes
sync always = yes
'''
Path('/run/smb.conf').write_text(config)
subprocess.run(['testparm','-s','/run/smb.conf'],check=True,stdout=subprocess.DEVNULL)
password=Path('/credentials/smb-password').read_text().strip()
subprocess.run(['smbpasswd','-c','/run/smb.conf','-s','-a','nas'],input=password+'\n'+password+'\n',text=True,check=True,stdout=subprocess.DEVNULL)
os.execv('/usr/sbin/smbd',['smbd','--foreground','--no-process-group','--configfile=/run/smb.conf','--debug-stdout'])
