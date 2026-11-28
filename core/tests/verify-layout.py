#!/usr/bin/env python3
"""Read-only verification of the NAS source/runtime directory contract."""
import sys
sys.dont_write_bytecode=True
import json,subprocess
from pathlib import Path
CORE=Path(__file__).resolve().parents[1];ROOT=CORE.parent
sys.path.insert(0,str(CORE/'scripts'))
config=json.loads(Path('/mnt/c/nas/system/config/config.json').read_text())
from nas_storage import configured,verify
native=configured(config)
directories={p.name for p in ROOT.iterdir() if p.is_dir() and p.name!='.git'}
assert directories==({'data','core','system','host'} if native and (ROOT/'host').is_dir() else {'data','core','system'})
if native:
 verify(config)
 assert (ROOT/'data').is_symlink() and (ROOT/'data').resolve()==Path(config['data'])
 if (ROOT/'host').exists():subprocess.run(['git','-C',str(ROOT),'check-ignore','-q','host'],check=True)
assert Path(subprocess.check_output(['git','-C',str(CORE),'rev-parse','--show-toplevel'],text=True).strip())==ROOT
assert config['root']==str(ROOT) and config['core']==str(CORE)
if not native:assert config['data']==str(ROOT/'data')
assert config['host']=='/mnt/c/nas/system' and config['backup_root']=='/mnt/c/nas/system/backups'
assert Path(config['image']).parent==Path('/mnt/c/nas/system/databases/immich')
if native:assert (ROOT/'data/shared').stat().st_dev==(ROOT/'data/shared/.stversions').stat().st_dev
else:assert (ROOT/'data/shared').stat().st_dev==(ROOT/'system/runtime').stat().st_dev
assert not list(CORE.rglob('__pycache__'))
assert not any(p.is_symlink() for p in CORE.rglob('*') if '.git' not in p.parts)
for name in ['config','secrets','state','cache','logs','backups','installed','exports','databases']:
 assert (ROOT/'system'/name).resolve()==Path('/mnt/c/nas/system')/name,name
for name in ['runtime','archive']:
 if native:assert (ROOT/'system'/name).resolve()==Path('/mnt/c/nas/system')/name
 else:assert (ROOT/'system'/name/'local').resolve()==Path('/mnt/c/nas/system')/name
files={str(p.relative_to(CORE)) for p in CORE.rglob('*') if p.is_file() and '.git' not in p.relative_to(CORE).parts}
tracked={p[5:] for p in subprocess.check_output(['git','-C',str(ROOT),'ls-files','-z'],text=True).strip('\0').split('\0') if p.startswith('core/')}
assert files==tracked,{'untracked':sorted(files-tracked),'missing':sorted(tracked-files)}
assert not any(p.startswith(('data/','system/','config/','secrets/','runtime/','installed/')) for p in tracked)
result={'state':'PASS','source_files':len(files),'checks':['root business directories are data/core/system','core is complete tracked source with no generated or linked files','runtime paths point to C system','database and backups remain on C','shared restore staging stays on its destination filesystem']}
(ROOT/'system/runtime/acceptance').mkdir(parents=True,exist_ok=True)
(ROOT/'system/runtime/acceptance/layout.json').write_text(json.dumps(result,indent=2))
print('PASS: directory contract;',len(files),'maintained files tracked in core')
