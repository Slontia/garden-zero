"""Initialize a private Joplin server account once; never overwrite user account changes."""
import sys
sys.dont_write_bytecode=True
import json,os,secrets,urllib.request,urllib.parse
from pathlib import Path

def prepare(c):
 marker=Path(c['host'])/'state/joplin-account.json'
 if marker.exists():return json.loads(marker.read_text())
 host=Path(c['host']);settings=dict(line.split('=',1) for line in (host/'config/joplin.env').read_text().splitlines() if '=' in line);base=settings['APP_BASE_URL'];authority=urllib.parse.urlsplit(base).netloc
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 def req(path,data=None,method=None,token=None):
  headers={'Host':authority,'Content-Type':'application/json'}
  if token:headers['X-API-AUTH']=token
  payload=json.dumps(data).encode() if data is not None else None
  r=urllib.request.Request('http://127.0.0.1:8082'+path,data=payload,headers=headers,method=method)
  with op.open(r,timeout=30) as response:
   raw=response.read();return json.loads(raw) if raw else None
 adminfile=host/'secrets/joplin-admin.json';userfile=host/'secrets/joplin-user.json'
 def createfile(path,email):
  if not path.exists():
   fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
   with os.fdopen(fd,'w') as stream:json.dump({'server':base,'email':email,'password':secrets.token_urlsafe(30)},stream,indent=2)
  return json.loads(path.read_text())
 admin=createfile(adminfile,'admin@nas.local');user=createfile(userfile,'notes@nas.local')
 try:session=req('/api/sessions',{'email':admin['email'],'password':admin['password']})
 except urllib.error.HTTPError:
  session=req('/api/sessions',{'email':'admin@localhost','password':'admin'})
  req('/api/users/'+session['user_id'],{'email':admin['email'],'password':admin['password'],'full_name':'NAS Administrator'},method='PATCH',token=session['id'])
  session=req('/api/sessions',{'email':admin['email'],'password':admin['password']})
 users=req('/api/users',token=session['id'])['items'];existing=next((u for u in users if u['email']==user['email']),None)
 if not existing:
  existing=req('/api/users',{'email':user['email'],'full_name':'NAS Notes'},token=session['id'])
  req('/api/users/'+existing['id'],{'password':user['password'],'email_confirmed':1,'must_set_password':0},method='PATCH',token=session['id'])
 req('/api/sessions',{'email':user['email'],'password':user['password']})
 (host/'state/joplin-account.json').write_text(json.dumps({'state':'ready','server':base,'email':user['email'],'admin_email':admin['email'],'credential_file':'system/secrets/joplin-user.json','default_password_replaced':True})+'\n')
 return json.loads((host/'state/joplin-account.json').read_text())

if __name__=='__main__':
 from nasctl import cfg,lock
 with lock():prepare(cfg())
 print('Joplin accounts initialized; credentials stored locally, not printed')
