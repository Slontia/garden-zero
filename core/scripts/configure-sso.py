#!/usr/bin/env python3
"""Configure one owner and bind existing app accounts. Secrets stay in private host files."""
import sys
sys.dont_write_bytecode=True
import argparse,json,os,secrets,subprocess,urllib.request,xml.etree.ElementTree as ET
from pathlib import Path
from nasctl import cfg,DOCKER,lock,compose,wait_services

def configure(c):
 host=Path(c['host']);remote=json.loads((host/'state/remote.json').read_text());origin=remote['meta'];name=remote['name']
 account=json.loads((host/'secrets/joplin-user.json').read_text())
 users=json.loads(subprocess.check_output(DOCKER+['exec','nas-database-1','psql','-U','postgres','-d','immich','-Atc','SELECT json_agg(json_build_object(\'id\',id,\'email\',email)) FROM public."user" WHERE "isAdmin"=true AND "deletedAt" IS NULL'],text=True))
 if not users or len(users)!=1:raise RuntimeError('Exactly one existing Immich administrator is required; refusing ambiguous account binding')
 secretfile=host/'secrets/sso-clients.json'
 if not secretfile.exists():
  fd=os.open(secretfile,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'w') as stream:json.dump({key:secrets.token_urlsafe(48) for key in ('meta','files','immich')},stream)
 from nas_database import sql
 joplin_users=json.loads(sql(c,"SELECT json_agg(json_build_object('id',id)) FROM users WHERE email='"+account['email'].replace("'","''")+"' AND is_admin=0;",'joplin',capture=True))
 if not joplin_users or len(joplin_users)!=1:raise RuntimeError('Exactly one existing non-admin Joplin user is required')
 prior=host/'state/sso-accounts.json'
 if prior.exists():
  previous=json.loads(prior.read_text())
  if previous.get('immich_user_id',users[0]['id'])!=users[0]['id'] or previous.get('joplin_user_id',joplin_users[0]['id'])!=joplin_users[0]['id']:raise RuntimeError('An existing application account changed; explicit recovery is required')
 clients=json.loads(secretfile.read_text())
 redirects={'meta':[origin+'/sso/callback'],'files':[remote['files']+'/sso/callback'],'immich':[remote['immich']+'/auth/login',remote['immich']+'/user-settings',remote['immich']+'/api/oauth/mobile-redirect','app.immich:///oauth-callback','app.immich://oauth-callback']}
 data={'owner_sub':c.get('sso',{}).get('owner_sub'),'password':(host/'secrets/admin-password.txt').read_text().strip(),'joplin_email':account['email'],'immich_email':users[0]['email'],'clients':clients,'redirects':redirects,'joplin_url':remote['knowledge']}
 code='DATA='+repr(data)+'\n'+(Path(c['root'])/'core/identity/setup.py').read_text()
 result=subprocess.run(DOCKER+['exec','-i','nas-authentik-server-1','ak','shell'],input=code,text=True,capture_output=True)
 (host/'runtime/authentik-setup.log').write_text(result.stdout+result.stderr)
 if result.returncode:raise RuntimeError('Identity configuration failed; inspect private authentik-setup.log')
 reports=[line.split('GARDEN_SSO_RESULT=',1)[1] for line in result.stdout.splitlines() if line.startswith('GARDEN_SSO_RESULT=')]
 if len(reports)!=1:raise RuntimeError('Identity setup did not produce a validated result')
 report=json.loads(reports[0])
 for key in ('meta','files'):
  settings={'issuer':origin+'/auth/application/o/'+key+'/','client_id':'garden-'+key,'client_secret':clients[key],'redirect_uri':redirects[key][0],'owner_sub':report['owner_uuid']}
  path=host/'config'/('sso-'+key+'.json');path.write_text(json.dumps(settings,indent=2)+'\n');path.chmod(0o600)
 request=urllib.request.Request('http://127.0.0.1:9000/auth/application/saml/joplin/metadata/',headers={'Host':name,'X-Forwarded-Proto':'https'})
 with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=30) as response:metadata=response.read()
 xml=ET.fromstring(metadata)
 if not xml.tag.endswith('EntityDescriptor'):raise RuntimeError('Unexpected SAML metadata')
 directory=host/'config/saml';directory.mkdir(exist_ok=True);(directory/'idp.xml').write_bytes(metadata)
 sp=ET.Element('{urn:oasis:names:tc:SAML:2.0:metadata}EntityDescriptor',entityID='Joplin');descriptor=ET.SubElement(sp,'{urn:oasis:names:tc:SAML:2.0:metadata}SPSSODescriptor',AuthnRequestsSigned='false',WantAssertionsSigned='true',protocolSupportEnumeration='urn:oasis:names:tc:SAML:2.0:protocol')
 ET.SubElement(descriptor,'{urn:oasis:names:tc:SAML:2.0:metadata}NameIDFormat').text='urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress'
 ET.SubElement(descriptor,'{urn:oasis:names:tc:SAML:2.0:metadata}AssertionConsumerService',Binding='urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST',Location=remote['knowledge']+'/api/saml',index='1')
 ET.ElementTree(sp).write(directory/'sp.xml',encoding='utf-8',xml_declaration=True)
 # Add only the new path; keep every existing private NAS route.
 current=json.loads(subprocess.check_output(DOCKER+['exec','nas-tailscale-1','tailscale','serve','status','--json'],text=True))
 web=current.get('Web',{}).get(name+':443',{}).get('Handlers',{})
 if web.get('/')!={'Proxy':'http://127.0.0.1:8080'}:raise RuntimeError('Unexpected management route; refusing replacement')
 if '/auth/' in web and web['/auth/']!={'Proxy':'http://127.0.0.1:9000/auth/'}:raise RuntimeError('Authentication route already belongs to another service')
 if '/auth/' not in web:subprocess.run(DOCKER+['exec','nas-tailscale-1','tailscale','serve','--bg','--yes','--https=443','--set-path=/auth/','http://127.0.0.1:9000/auth/'],check=True,stdout=subprocess.DEVNULL)
 c['sso']={**c.get('sso',{}),'owner_sub':report['owner_uuid'],'clients_prepared':True}
 (host/'config/config.json').write_text(json.dumps(c,indent=2)+'\n')
 (host/'state/sso-accounts.json').write_text(json.dumps({'state':'enabled_pending_acceptance' if c.get('sso',{}).get('enabled') else 'configured_pending_acceptance','owner_uuid':report['owner_uuid'],'immich_user_id':users[0]['id'],'joplin_email':account['email'],'joplin_user_id':joplin_users[0]['id'],'account_creation_disabled':True},indent=2)+'\n')
 print('Identity providers configured; existing app identities selected; private /auth/ route preserved')

def app_request(host,path,data=None,token=None,method=None):
 headers={'Content-Type':'application/json'}
 if token:headers['Authorization']='Bearer '+token
 request=urllib.request.Request('http://127.0.0.1:2283/api'+path,data=json.dumps(data).encode() if data is not None else None,headers=headers,method=method)
 try:
  with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=45) as response:return json.load(response)
 except urllib.error.HTTPError as error:
  (host/'runtime/sso-app-error.json').write_bytes(error.read());raise RuntimeError('Application rejected SSO settings; inspect private sso-app-error.json') from None

def activate(c):
 from nas_database import sql
 import importlib.util
 host=Path(c['host']);accounts=json.loads((host/'state/sso-accounts.json').read_text())
 if not c.get('sso',{}).get('clients_prepared'):raise RuntimeError('Configure and verify identity providers first')
 email=accounts['joplin_email'].replace("'","''")
 users=json.loads(sql(c,"SELECT json_agg(json_build_object('id',id,'is_external',is_external,'is_admin',is_admin,'password',password,'email_confirmed',email_confirmed,'must_set_password',must_set_password)) FROM users WHERE email='"+email+"';",'joplin',capture=True))
 if not users or len(users)!=1 or users[0]['is_admin'] or accounts.get('joplin_user_id',users[0]['id'])!=users[0]['id']:raise RuntimeError('A unique non-admin Joplin account is required')
 login=app_request(host,'/auth/login',{'email':json.loads(sql(c,"SELECT row_to_json(u) FROM (SELECT email FROM public.\"user\" WHERE id='"+accounts['immich_user_id']+"') u;",'immich',capture=True))['email'],'password':(host/'secrets/admin-password.txt').read_text().strip()})
 token=login['accessToken'];current=app_request(host,'/system-config',token=token)
 snapshot=host/'secrets/sso-rollback.json'
 if not snapshot.exists():
  fd=os.open(snapshot,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'w') as stream:json.dump({'immich_oauth':current['oauth'],'immich_file':(host/'config/immich.json').read_text(),'joplin_user':users[0],'joplin_env':(host/'config/joplin.env').read_text()},stream)
 saved=json.loads(snapshot.read_text())
 if 'immich_file' not in saved:
  saved['immich_file']=(host/'config/immich.json').read_text();snapshot.write_text(json.dumps(saved))
 clients=json.loads((host/'secrets/sso-clients.json').read_text());origin=c['sso']['origin']
 current['oauth'].update({'enabled':True,'autoLaunch':True,'autoRegister':False,'clientId':'garden-immich','clientSecret':clients['immich'],'issuerUrl':origin+'/auth/application/o/immich/','buttonText':'登录零号庭院','scope':'openid email profile','signingAlgorithm':'RS256','tokenEndpointAuthMethod':'client_secret_post'})
 configpath=host/'config/immich.json';settings=json.loads(configpath.read_text());settings['oauth']=current['oauth'];configpath.write_text(json.dumps(settings,indent=2)+'\n')
 env=host/'config/joplin.env';lines=[line for line in env.read_text().splitlines() if not line.startswith(('SAML_','ORGANIZATION_DISPLAY_NAME=','DELETE_EXPIRED_SESSIONS_SCHEDULE='))]
 lines+=['SAML_ENABLED=true','SAML_IDP_CONFIG_FILE=/config/saml/idp.xml','SAML_SP_CONFIG_FILE=/config/saml/sp.xml','ORGANIZATION_DISPLAY_NAME=零号庭院','DELETE_EXPIRED_SESSIONS_SCHEDULE=']
 env.write_text('\n'.join(lines)+'\n');env.chmod(0o600)
 sql(c,"UPDATE users SET is_external=1,password='',email_confirmed=1,must_set_password=0 WHERE id='"+users[0]['id'].replace("'","''")+"' AND is_admin=0;",'joplin')
 c['sso']['enabled']=True;(host/'config/config.json').write_text(json.dumps(c,indent=2)+'\n')
 module=Path(__file__).with_name('write-environment.py');spec=importlib.util.spec_from_file_location('environment',module);environment=importlib.util.module_from_spec(spec);spec.loader.exec_module(environment);environment.generate(c)
 compose(c,'up','-d','--no-build','--force-recreate','--wait','--wait-timeout','180','meta','joplin','immich-server');wait_services(c,['meta','joplin','immich-server'])
 accounts.update({'state':'enabled_pending_acceptance','joplin_user_id':users[0]['id']});(host/'state/sso-accounts.json').write_text(json.dumps(accounts,indent=2)+'\n')
 print('Unified login enabled; existing account IDs retained; local administrator recovery retained')

def rollback(c):
 """Restore prior login methods without restoring or replacing user content."""
 from nas_database import sql
 import importlib.util
 host=Path(c['host']);saved=json.loads((host/'secrets/sso-rollback.json').read_text());accounts=json.loads((host/'state/sso-accounts.json').read_text())
 settings=json.loads((host/'config/immich.json').read_text());settings.pop('oauth',None)
 # Preserve non-authentication settings changed after initial rollout.
 settings.update({'oauth':json.loads(saved.get('immich_file','{}'))['oauth']} if 'oauth' in json.loads(saved.get('immich_file','{}')) else {});(host/'config/immich.json').write_text(json.dumps(settings,indent=2)+'\n')
 user=saved['joplin_user'];sql(c,"UPDATE users SET is_external="+str(int(user['is_external']))+",password='"+user['password'].replace("'","''")+"',email_confirmed="+str(int(user['email_confirmed']))+",must_set_password="+str(int(user['must_set_password']))+" WHERE id='"+user['id'].replace("'","''")+"' AND is_admin=0;",'joplin')
 env=host/'config/joplin.env';prefixes=('SAML_','ORGANIZATION_DISPLAY_NAME=','DELETE_EXPIRED_SESSIONS_SCHEDULE=');lines=[line for line in env.read_text().splitlines() if not line.startswith(prefixes)]+[line for line in saved['joplin_env'].splitlines() if line.startswith(prefixes)];env.write_text('\n'.join(lines)+'\n');c['sso']['enabled']=False;(host/'config/config.json').write_text(json.dumps(c,indent=2)+'\n')
 spec=importlib.util.spec_from_file_location('environment',Path(__file__).with_name('write-environment.py'));environment=importlib.util.module_from_spec(spec);spec.loader.exec_module(environment);environment.generate(c)
 compose(c,'up','-d','--no-build','--force-recreate','--wait','--wait-timeout','180','meta','joplin','immich-server')
 accounts['state']='rolled_back';(host/'state/sso-accounts.json').write_text(json.dumps(accounts,indent=2)+'\n')
 print('Prior login methods restored; user IDs and content preserved')

if __name__=='__main__':
 parser=argparse.ArgumentParser();group=parser.add_mutually_exclusive_group();group.add_argument('--activate',action='store_true');group.add_argument('--rollback',action='store_true');args=parser.parse_args()
 with lock():
  c=cfg()
  if args.rollback:rollback(c)
  elif args.activate:activate(c)
  else:configure(c)
