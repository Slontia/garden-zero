#!/usr/bin/env python3
"""Read-only verification of the NAS source/runtime directory contract."""
import sys
sys.dont_write_bytecode=True
import json,subprocess
from pathlib import Path
CORE=Path(__file__).resolve().parents[1];ROOT=CORE.parent
config=json.loads(Path('/mnt/c/nas/system/config/config.json').read_text())
assert {p.name for p in ROOT.iterdir() if p.is_dir() and p.name!='.git'}=={'data','core','system'}
assert Path(subprocess.check_output(['git','-C',str(CORE),'rev-parse','--show-toplevel'],text=True).strip())==ROOT
assert config['root']==str(ROOT) and config['data']==str(ROOT/'data') and config['core']==str(CORE)
assert config['host']=='/mnt/c/nas/system' and config['backup_root']=='/mnt/c/nas/system/backups'
assert Path(config['image']).parent==Path('/mnt/c/nas/system/databases/immich')
assert (ROOT/'data/shared').stat().st_dev==(ROOT/'system/runtime').stat().st_dev
assert not list(CORE.rglob('__pycache__'))
assert not any(p.is_symlink() for p in CORE.rglob('*') if '.git' not in p.parts)
for name in ['config','secrets','state','cache','logs','backups','installed','exports','databases']:
 assert (ROOT/'system'/name).resolve()==Path('/mnt/c/nas/system')/name,name
for name in ['runtime','archive']:
 assert (ROOT/'system'/name/'local').resolve()==Path('/mnt/c/nas/system')/name
files={str(p.relative_to(CORE)) for p in CORE.rglob('*') if p.is_file() and '.git' not in p.relative_to(CORE).parts}
tracked={p[5:] for p in subprocess.check_output(['git','-C',str(ROOT),'ls-files','-z'],text=True).strip('\0').split('\0') if p.startswith('core/')}
assert files==tracked,{'untracked':sorted(files-tracked),'missing':sorted(tracked-files)}
assert not any(p.startswith(('data/','system/','config/','secrets/','runtime/','installed/')) for p in tracked)
result={'state':'PASS','source_files':len(files),'checks':['root business directories are data/core/system','core is complete tracked source with no generated or linked files','runtime junctions point to C system','database and backups remain on C','shared restore staging stays on data filesystem']}
(ROOT/'system/runtime/acceptance/layout.json').write_text(json.dumps(result,indent=2))
print('PASS: directory contract;',len(files),'maintained files tracked in core')
