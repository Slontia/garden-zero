"""Run isolated JavaScript checks without depending on an enabled NAS application."""
import sys
sys.dont_write_bytecode=True
import json,shutil,subprocess
from pathlib import Path

def run_node(script,payload):
 node=shutil.which('node')
 if node:command=[node,'-e',script]
 else:
  image=json.loads(Path('/mnt/c/nas/system/config/versions.json').read_text())['IMMICH_SERVER_IMAGE']['image']
  command=['sudo','-n','docker','--host','unix:///var/run/docker.sock','run','--pull=never','--rm','-i','--network','none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--memory=128m','--entrypoint','node',image,'-e',script]
 result=subprocess.run(command,input=json.dumps(payload),capture_output=True,text=True)
 if result.returncode:raise RuntimeError(result.stderr.strip())
 return result.stdout
