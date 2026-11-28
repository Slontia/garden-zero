"""Shared PostgreSQL roles, private per-app credentials and explicit existing-instance migration."""
import sys
sys.dont_write_bytecode=True
import json,os,re,secrets,subprocess
from pathlib import Path

def application_databases(c):
 from nas_apps import APPS
 return tuple(dict.fromkeys(app['database'] for app in APPS.values() if app.get('database') and
  (not app.get('database_activation_config') or (Path(c['host'])/'config'/app['database_activation_config']).exists() or c.get('sso',{}).get('prepared'))))

def primary_database():
 from nas_apps import APPS
 return next(app['database'] for app in APPS.values() if app.get('backup_primary_database'))

def credentials(c,migrate=False):
 host=Path(c['host']);secret=host/'secrets'
 for name in (key+'-db-password' for key in application_databases(c)):
  path=secret/name
  if path.is_symlink():raise RuntimeError('Linked database credential refused')
  if not path.exists():
   fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
   with os.fdopen(fd,'w') as stream:stream.write(secrets.token_hex(32)+'\n')
 passwords={name:(secret/(name+'-db-password')).read_text().strip() for name in application_databases(c)}
 if any(not re.fullmatch('[a-f0-9]{64}',v) for v in passwords.values()):raise RuntimeError('Unexpected application database credential format')
 remote=json.loads((host/'state/remote.json').read_text()) if (host/'state/remote.json').exists() else {}
 base=remote.get('knowledge','http://127.0.0.1:8082')
 settings={'immich.env':'DB_PASSWORD='+passwords['immich']+'\n','joplin.env':'POSTGRES_PASSWORD='+passwords['joplin']+'\nAPP_BASE_URL='+base+'\n'}
 for name,value in settings.items():
  path=host/'config'/name
  if path.is_symlink():raise RuntimeError('Linked application environment refused')
  if path.exists() and path.read_text()!=value:
   existing=dict(line.split('=',1) for line in path.read_text().splitlines() if '=' in line and not line.startswith('#'))
   expected=dict(line.split('=',1) for line in value.splitlines() if '=' in line)
   if all(existing.get(k)==v for k,v in expected.items()):continue
   if not migrate:raise RuntimeError('Existing app environment differs; explicit database migration required: '+name)
   saved=host/'state'/(name+'.before-shared-postgres')
   if not saved.exists():saved.write_bytes(path.read_bytes());saved.chmod(0o600)
  path.write_text(value);path.chmod(0o600)
 return passwords

def sql(c,query,database='postgres',capture=False):
 from nasctl import DOCKER
 args=DOCKER+['exec','-i',c['project']+'-database-1','psql','-X','-v','ON_ERROR_STOP=1','-U','postgres','-d',database,'-At']
 result=subprocess.run(args,input=query,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 if result.returncode:raise RuntimeError('Shared PostgreSQL provisioning failed; inspect local database logs')
 return result.stdout.strip() if capture else None

def provision(c):
 """Only call while holding database_lock and after verified mount/readiness."""
 host=Path(c['host']);passwords={name:(host/'secrets'/(name+'-db-password')).read_text().strip() for name in application_databases(c)}
 if any(not re.fullmatch('[a-f0-9]{64}',v) for v in passwords.values()):raise RuntimeError('Invalid application database credentials')
 for name,password in passwords.items():
  sql(c,f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{name}') THEN CREATE ROLE {name} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION; END IF; END $$; ALTER ROLE {name} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '{password}';")
  exists=sql(c,f"SELECT 1 FROM pg_database WHERE datname='{name}';",capture=True)
  if not exists:sql(c,f'CREATE DATABASE {name} OWNER {name};')
  sql(c,f'ALTER DATABASE {name} OWNER TO {name}; REVOKE ALL ON DATABASE {name} FROM PUBLIC; GRANT CONNECT,TEMPORARY ON DATABASE {name} TO {name};')
  sql(c,f'REVOKE CREATE ON SCHEMA public FROM PUBLIC; GRANT ALL ON SCHEMA public TO {name};',name)
 # Remove default cross-database connectivity, including the maintenance database.
 sql(c,'REVOKE ALL ON DATABASE postgres FROM PUBLIC; REVOKE CONNECT ON DATABASE template1 FROM PUBLIC;')
 sql(c,'CREATE EXTENSION IF NOT EXISTS vchord CASCADE; CREATE EXTENSION IF NOT EXISTS earthdistance CASCADE; CREATE EXTENSION IF NOT EXISTS pg_trgm; CREATE EXTENSION IF NOT EXISTS unaccent; CREATE EXTENSION IF NOT EXISTS "uuid-ossp";', 'immich')
 # Transfer only Immich application objects, never shared objects or extension-owned objects.
 sql(c,"""BEGIN;
 ALTER SCHEMA public OWNER TO immich;
 DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT c.oid,c.relname,c.relkind FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid
 WHERE n.nspname='public' AND pg_get_userbyid(c.relowner)='postgres' AND c.relkind IN ('r','p','v','m','f')
 AND NOT EXISTS(SELECT FROM pg_depend d WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND d.deptype='e') LOOP
 EXECUTE format('ALTER %s public.%I OWNER TO immich',CASE r.relkind WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'f' THEN 'FOREIGN TABLE' ELSE 'TABLE' END,r.relname); END LOOP;
 FOR r IN SELECT p.oid::regprocedure AS signature FROM pg_proc p JOIN pg_namespace n ON p.pronamespace=n.oid
 WHERE n.nspname='public' AND pg_get_userbyid(p.proowner)='postgres'
 AND NOT EXISTS(SELECT FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e') LOOP
 EXECUTE format('ALTER FUNCTION %s OWNER TO immich',r.signature); END LOOP;
 FOR r IN SELECT t.typname FROM pg_type t JOIN pg_namespace n ON t.typnamespace=n.oid
 WHERE n.nspname='public' AND t.typtype='e' AND pg_get_userbyid(t.typowner)='postgres' LOOP
 EXECUTE format('ALTER TYPE public.%I OWNER TO immich',r.typname); END LOOP;
 END $$;
 COMMIT;""",'immich')
 (host/'state/shared-postgres.json').write_text(json.dumps({'schema':1,'databases':list(application_databases(c)),'roles':list(application_databases(c)),'isolated':True})+'\n')
