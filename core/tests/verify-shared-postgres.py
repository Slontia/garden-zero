#!/usr/bin/env python3
"""Live read-only role checks and a temporary Joplin sync item, removed on exit."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import json,subprocess,urllib.request,urllib.parse,uuid
from nas_database import application_databases
from nasctl import cfg,DOCKER,db_signature

def main():
 c=cfg();host=Path(c['host']);checks=[]
 def login(role,database,password):
  command=DOCKER+['exec','-i',c['project']+'-database-1','sh','-c','IFS= read -r PGPASSWORD; export PGPASSWORD; exec psql "$@"','psql','-h','database','-U',role,'-d',database,'-Atc','SELECT current_user']
  return subprocess.run(command,input=password+'\n',text=True,capture_output=True)
 databases=application_databases(c)
 for role in databases:
  other=next(name for name in databases if name!=role)
  password=(host/'secrets'/(role+'-db-password')).read_text().strip()
  assert login(role,role,password).stdout.strip()==role
  assert all(login(role,other,password).returncode!=0 for other in databases if other!=role)
  assert login(role,'postgres',password).returncode!=0
  assert login(role,role,'incorrect-password').returncode!=0
  checks.append(role+': own database succeeds; other app/maintenance database and wrong password rejected')
 roles=json.loads(subprocess.check_output(DOCKER+['exec',c['project']+'-database-1','psql','-U','postgres','-d','postgres','-Atc',"SELECT json_agg(json_build_object('role',rolname,'super',rolsuper,'createdb',rolcreatedb,'createrole',rolcreaterole)) FROM pg_roles WHERE rolname IN ('immich','joplin','platform_auth')"],text=True))
 assert len(roles)==len(databases) and all(not r['super'] and not r['createdb'] and not r['createrole'] for r in roles);checks.append('all app roles are non-superusers without role/database creation privileges')
 saved=host/'state/shared-postgres-migration/immich-table-counts.json'
 if saved.exists():
  before=json.loads(saved.read_text());now=db_signature(c['project']+'-database-1')
  # Operational queue rows can change after normal startup; user content must not.
  for table in ('asset','user','album'):assert now.get(table)==before.get(table)
  checks.append('Immich asset/user/album row counts preserved')
 credentials=json.loads((host/'secrets/joplin-user.json').read_text());authority=urllib.parse.urlsplit(credentials['server']).netloc
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}));token=None
 def req(path,data=None,method=None):
  headers={'Host':authority}
  if token:headers['X-API-AUTH']=token
  if isinstance(data,dict):data=json.dumps(data).encode();headers['Content-Type']='application/json'
  elif isinstance(data,bytes) and method=='PUT':
   boundary='nas'+uuid.uuid4().hex
   headers['Content-Type']='multipart/form-data; boundary='+boundary
   data=('--'+boundary+'\r\nContent-Disposition: form-data; name="file"; filename="item.md"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()+data+('\r\n--'+boundary+'--\r\n').encode()
  r=urllib.request.Request('http://127.0.0.1:8082'+path,data=data,headers=headers,method=method)
  with op.open(r,timeout=30) as response:return response.read()
 if c.get('sso',{}).get('enabled'):
  try:req('/api/sessions',{'email':credentials['email'],'password':credentials['password']})
  except urllib.error.HTTPError as error:
   assert error.code in (400,403);error.close();checks.append('external daily Joplin account rejects its prior local password')
  else:raise RuntimeError('External daily account still permits local password login')
  admin=json.loads((host/'secrets/joplin-admin.json').read_text());recovery=json.loads(req('/api/sessions',{'email':admin['email'],'password':admin['password']}))
  from nas_database import sql
  sql(c,"DELETE FROM sessions WHERE id='"+recovery['id'].replace("'","''")+"';",'joplin');checks.append('independent Joplin administrator password recovery still works')
 sessionfile=host/'secrets/joplin-sso-test-session.json'
 if c.get('sso',{}).get('enabled'):
  if not sessionfile.is_file():raise RuntimeError('Run verify-sso-browser.ps1 first to authorize a temporary client session')
  session=json.loads(sessionfile.read_text())
 else:session=json.loads(req('/api/sessions',{'email':credentials['email'],'password':credentials['password']}))
 token=session['id']
 expected=subprocess.check_output(DOCKER+['exec',c['project']+'-database-1','psql','-U','postgres','-d','joplin','-Atc',"SELECT id FROM users WHERE email='"+credentials['email'].replace("'","''")+"' AND is_admin=0"],text=True).strip()
 assert session['user_id']==expected;checks.append('authorized session belongs to the existing non-admin Joplin account')
 identifier=uuid.uuid4().hex;path='/api/items/root:/'+identifier+'.md:';content=('NAS temporary sync acceptance\n\n# Markdown\n\nPrivate sync item.\n\nid: '+identifier+'\ntype_: 1').encode()
 try:
  req(path+'/content',content,'PUT');download=req(path+'/content');assert b'Private sync item.' in download and identifier.encode() in download and b'type_: 1' in download;checks.append('non-admin account uploads and retrieves Markdown body and item identity through Joplin sync API')
 finally:
  req(path,method='DELETE')
  if c.get('sso',{}).get('enabled'):
   from nas_database import sql
   sql(c,"DELETE FROM sessions WHERE id='"+token.replace("'","''")+"' AND user_id='"+expected.replace("'","''")+"';",'joplin');sessionfile.unlink()
 rows=json.loads(subprocess.check_output(DOCKER+['ps','--filter','label=com.docker.compose.project='+c['project'],'--filter','label=com.docker.compose.service=database','--format','{{json .}}'],text=True).strip())
 assert rows;checks.append('one shared NAS PostgreSQL container')
 out=host/'state/shared-postgres-migration/acceptance.json';out.write_text(json.dumps({'state':'PASS','checks':checks},indent=2)+'\n')
 print('PASS:',len(checks),'shared database/account/sync checks; temporary item removed')
if __name__=='__main__':main()
