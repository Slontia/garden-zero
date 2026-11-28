"""Shared registered configuration/SQLite capture; never stops applications."""
import tarfile,tempfile
from pathlib import Path
from nas_backup_control import check

def archive_config(c,destination):
 stage=Path(destination).parent
 with tarfile.open(destination,'w') as tar:
  original_add=tar.add
  def checked_add(*args,**kwargs):
   check();previous_filter=kwargs.get('filter')
   def checked_filter(member):
    check();return previous_filter(member) if previous_filter else member
   kwargs['filter']=checked_filter
   return original_add(*args,**kwargs)
  tar.add=checked_add
  tar.add(Path(c['host'])/'config',arcname='system/config');tar.add(Path(c['host'])/'secrets',arcname='system/secrets',filter=lambda m:None if m.name.endswith('/hdd-mirror-key') else m)
  from nas_apps import APPS
  from nas_mirror import relative
  for resource in dict.fromkeys(path for app in APPS.values() for path in app.get('backup_state_paths',[])):
   check();resource=relative(resource);p=Path(c['host'])/resource
   if p.exists():tar.add(p,arcname='system/'+resource)
  for directory in ('web','identity','scripts','tests','services','templates','host','docs','compose'):
   tar.add(Path(c['root'])/'core'/directory,arcname='core/'+directory,filter=lambda m:None if '__pycache__' in m.name else m)
  for filename in ('README.md','deploy.sh','start.sh','stop.sh','start.ps1','stop.ps1','.env.example','.gitignore','.gitattributes'):
   tar.add(Path(c['root'])/'core'/filename,arcname='core/'+filename)
  for filename in ('README.md','LICENSE','.gitignore','.gitattributes','.editorconfig','CONTRIBUTING.md','AGENTS.md','SECURITY.md','THIRD_PARTY_NOTICES.md'):
   p=Path(c['root'])/filename
   if p.is_file():tar.add(p,arcname='project/'+filename)
  # Registered SQLite resources use online backup, never raw live-file copies.
  import sqlite3
  for resource in dict.fromkeys(path for app in APPS.values() for path in app.get('backup_sqlite_paths',[])):
   check();resource=relative(resource);database_path=Path(c['host'])/resource
   if database_path.is_file():
    with tempfile.TemporaryDirectory(dir=stage) as temp:
     destination=Path(temp)/'snapshot.sqlite'
     with sqlite3.connect('file:'+str(database_path)+'?mode=ro',uri=True) as source_db,sqlite3.connect(destination) as target_db:
      source_db.backup(target_db,pages=256,progress=lambda *args:check())
     tar.add(destination,arcname='system/'+resource)
