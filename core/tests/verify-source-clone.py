#!/usr/bin/env python3
"""Finite isolated clone/initialization checks for the data/core/system contract."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import ast,datetime,hashlib,importlib.util,json,os,shutil,subprocess,tokenize,uuid
ROOT=Path(__file__).resolve().parents[2];CORE=ROOT/'core';HOST=Path('/mnt/c/nas/system');PS='/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe'
def command(args):
 args=[str(x) for x in args]
 if args[0]=='git' and '-C' in args:args[1:1]=['-c','safe.directory='+args[args.index('-C')+1]]
 if args[0]=='git' and 'clone' in args:args[1:1]=['-c','safe.directory='+str(Path(args[-2])/'.git'),'-c','safe.directory='+args[-2]]
 return args
def run(args,**kw):return subprocess.run(command(args),check=True,**kw)
def out(args):return subprocess.check_output(command(args),text=True).strip()
def fingerprint():
 return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [HOST/'config/config.json',HOST/'config/compose.env',*list((HOST/'secrets').glob('*'))] if p.is_file()}
def main():
 stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6];work=ROOT/'system/runtime/tests'/('source-check-'+stamp);public=work/'candidate';clone=work/'instance';public.mkdir(parents=True);fixture=HOST/'runtime'/('source-check-'+stamp);fixture.mkdir();(fixture/'state').mkdir();(fixture/'config').mkdir();(fixture/'secrets').mkdir();before=fingerprint();checks=[]
 paths=out(['git','-C',ROOT,'ls-files','--cached','--others','--exclude-standard','-z']).split('\0')
 for name in paths:
  if not name:continue
  p=ROOT/name
  if p.is_symlink() or '__pycache__' in p.parts:raise RuntimeError('Generated or linked file in core: '+name)
  q=public/name;q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q)
 run(['git','init','-q',public]);run(['git','-C',public,'add','.']);run([sys.executable,'-B',public/'core/scripts/check-staged.py']);checks.append('public source passes secret/path guard')
 run(['git','-C',public,'-c','user.name=NAS validation','-c','user.email=validation@localhost','commit','-qm','Isolated source validation']);run(['git','clone','-q','--no-hardlinks',public,clone]);checks.append('real independent clone keeps data/system outside Git')
 assert (clone/'core/templates/data-access.json').is_file();checks.append('directory ownership registry ships with source')
 for line in (clone/'core/web/Dockerfile').read_text().splitlines():
  if line.startswith('COPY '):
   for name in line.split()[1:-1]:assert (clone/'core/web'/name).is_file(),name
 checks.append('all web image assets ship with source')
 install=ast.parse((clone/'core/scripts/install-host.py').read_text());tools=next(ast.literal_eval(n.value) for n in install.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TOOLS' for t in n.targets))
 for name in tools:assert (clone/'core/scripts'/name).is_file(),name
 checks.append('all installed host dependencies ship with source')
 for p in clone.rglob('*.py'):ast.parse(tokenize.open(p).read(),filename=str(p))
 for p in clone.rglob('*.sh'):run(['bash','-n',p])
 checks.append('Python and Shell syntax')
 winclone=out(['wslpath','-w',clone]);code="$errs=@(); Get-ChildItem -Recurse -LiteralPath '"+winclone.replace("'","''")+"' -Filter '*.ps1' | ForEach-Object {$e=$null;[System.Management.Automation.Language.Parser]::ParseFile($_.FullName,[ref]$null,[ref]$e)>$null;$errs+=@($e)};if($errs.Count){$errs|ForEach-Object {$_.Message};exit 1}"
 run([PS,'-NoProfile','-NonInteractive','-Command',code],stdout=subprocess.DEVNULL);checks.append('Windows script syntax')
 refused=subprocess.run([sys.executable,'-B',clone/'core/scripts/nasctl.py','status'],capture_output=True,text=True)
 assert refused.returncode!=0 and 'another instance' in refused.stderr;checks.append('cloned controller refuses production instance')
 prepare=winclone+'\\core\\scripts\\prepare-windows-host.ps1';receipt=json.loads(next(x for x in out([PS,'-NoProfile','-NonInteractive','-File',prepare,'-CheckOnly']).splitlines() if x.startswith('{')))
 assert receipt['root_wsl']==str(clone);checks.append('Windows preparation derives enclosing NAS root')
 refused=subprocess.run([PS,'-NoProfile','-NonInteractive','-File',prepare,'-CreateDatabaseImage'],capture_output=True,text=True,errors='replace')
 assert refused.returncode!=0 and 'Another checkout owns' in refused.stderr;checks.append('preparation refuses takeover before changes')
 spec=importlib.util.spec_from_file_location('isolated_initializer',clone/'core/scripts/initialize.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.HOST=fixture;m.INTERNAL_ROOT=fixture/'databases/immich';m.INTERNAL_ROOT.mkdir(parents=True)
 image=m.INTERNAL_ROOT/'postgres.img';image.touch();run(['/mnt/c/WINDOWS/System32/fsutil.exe','sparse','setflag',out(['wslpath','-w',image])],stdout=subprocess.DEVNULL)
 with image.open('r+b') as stream:stream.truncate(64*1024**3)
 assert image.stat().st_blocks==0;receipt.update(image_wsl=str(image));(fixture/'state/prepared-host.json').write_text(json.dumps(receipt));p=m.plan(fixture);assert p['physical_disk_format'] is False and p['root']==str(clone);checks.append('fresh plan accepts only empty sparse file and correct layout')
 def reject(name,setup,cleanup):
  setup();snapshot=image.stat()
  try:m.plan(fixture)
  except Exception:checks.append(name)
  else:raise AssertionError(name)
  assert image.stat().st_size==snapshot.st_size
  cleanup()
 reject('existing config refused',lambda:(fixture/'config/config.json').write_text('{}'),lambda:(fixture/'config/config.json').unlink())
 reject('existing secrets refused',lambda:(fixture/'secrets/existing').write_text('fixture'),lambda:(fixture/'secrets/existing').unlink())
 reject('interrupted initialization refused',lambda:(fixture/'state/initialization-started.json').write_text('{}'),lambda:(fixture/'state/initialization-started.json').unlink())
 original=(fixture/'state/prepared-host.json').read_bytes()
 reject('wrong volume identity refused',lambda:(fixture/'state/prepared-host.json').write_text(json.dumps({**receipt,'volume_guid':'00000000-0000-0000-0000-000000000000'})),lambda:(fixture/'state/prepared-host.json').write_bytes(original))
 reject('other checkout receipt refused',lambda:(fixture/'state/prepared-host.json').write_text(json.dumps({**receipt,'root_wsl':'/mnt/d/another-instance'})),lambda:(fixture/'state/prepared-host.json').write_bytes(original))
 def allocate():
  with image.open('r+b') as stream:
   stream.write(b'existing fixture data');stream.flush();os.fsync(stream.fileno())
 reject('allocated image refused',allocate,lambda:None);image.unlink();assert fingerprint()==before;checks.append('production config and credentials unchanged')
 assert not list(clone.rglob('__pycache__'));checks.append('validation leaves no generated files inside core')
 result={'state':'PASS','checks':checks,'scope':'isolated source and initialization plan only; no formatting, second NAS or production stop','work':str(work)};(work/'result.json').write_text(json.dumps(result,indent=2));(ROOT/'system/runtime/acceptance/source-clone.json').write_text(json.dumps(result,indent=2));print('PASS:',len(checks),'source/layout safety checks. Evidence: '+str(work/'result.json'))
if __name__=='__main__':main()
