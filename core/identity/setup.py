"""Executed by authentik's management shell. DATA is supplied privately over stdin."""
import json
from django.db import transaction
from authentik.core.models import User,Application
from authentik.flows.models import Flow,FlowStageBinding
from authentik.stages.password.models import PasswordStage
from authentik.stages.user_login.models import UserLoginStage
from authentik.policies.expression.models import ExpressionPolicy
from authentik.policies.models import PolicyBinding
from authentik.policies.reputation.models import ReputationPolicy
from authentik.brands.models import Brand
from authentik.providers.oauth2.models import OAuth2Provider,ScopeMapping,RedirectURI,RedirectURIMatchingMode,RedirectURIType
from authentik.providers.saml.models import SAMLProvider,SAMLPropertyMapping
from authentik.crypto.models import CertificateKeyPair

with transaction.atomic():
 owner,created=User.objects.get_or_create(username='owner',defaults={'name':'零号庭院主人','email':DATA['joplin_email'],'is_active':True})
 if DATA.get('owner_sub') and str(owner.uuid)!=DATA['owner_sub']:raise RuntimeError('Existing owner identity changed; explicit recovery is required')
 if created:owner.set_password(DATA['password']);owner.save()
 flow,_=Flow.objects.update_or_create(slug='garden-login',defaults={'name':'零号庭院登录','title':'欢迎回到零号庭院','designation':'authentication','authentication':'none','denied_action':'message'})
 password,_=PasswordStage.objects.update_or_create(name='garden-password',defaults={'backends':['authentik.core.auth.InbuiltBackend']})
 login,_=UserLoginStage.objects.update_or_create(name='garden-login-session',defaults={'session_duration':'days=14','remember_me_offset':'seconds=0'})
 password_binding,_=FlowStageBinding.objects.update_or_create(target=flow,order=10,defaults={'stage':password,'evaluate_on_plan':True,'re_evaluate_policies':True})
 FlowStageBinding.objects.update_or_create(target=flow,order=20,defaults={'stage':login,'evaluate_on_plan':False,'re_evaluate_policies':True})
 identify,_=ExpressionPolicy.objects.update_or_create(name='garden-fixed-owner',defaults={'expression':"user = ak_user_by(username='owner')\nrequest.context['pending_user'] = user\nif 'flow_plan' in request.context:\n    request.context['flow_plan'].context['pending_user'] = user\nreturn user is not None"})
 PolicyBinding.objects.filter(target=flow,order=0,policy=identify).delete()
 PolicyBinding.objects.update_or_create(target=password_binding,order=0,defaults={'policy':identify,'enabled':True,'timeout':30})
 limit,_=ReputationPolicy.objects.update_or_create(name='garden-login-limit',defaults={'check_ip':True,'check_username':False,'threshold':-10})
 PolicyBinding.objects.update_or_create(target=flow,order=1,defaults={'policy':limit,'enabled':True,'negate':True,'timeout':30})
 allow,_=ExpressionPolicy.objects.update_or_create(name='garden-owner-only',defaults={'expression':"return request.user.is_authenticated and request.user.username == 'owner'"})
 authorization=Flow.objects.get(slug='default-provider-authorization-implicit-consent')
 invalidation=Flow.objects.get(slug='default-invalidation-flow')
 certificate=CertificateKeyPair.objects.filter(name='authentik Self-signed Certificate').first()
 if not certificate:raise RuntimeError('Signing certificate not initialized')
 profile=ScopeMapping.objects.get(managed='goauthentik.io/providers/oauth2/scope-profile')
 openid=ScopeMapping.objects.get(managed='goauthentik.io/providers/oauth2/scope-openid')
 providers={}
 for key in ('meta','files','immich'):
  email=DATA['immich_email'] if key=='immich' else DATA['joplin_email']
  mapping,_=ScopeMapping.objects.update_or_create(name='garden-'+key+'-email',defaults={'scope_name':'email','expression':'return '+repr({'email':email,'email_verified':True})})
  provider,_=OAuth2Provider.objects.update_or_create(name='garden-'+key,defaults={'authentication_flow':flow,'authorization_flow':authorization,'invalidation_flow':invalidation,'client_id':'garden-'+key,'client_secret':DATA['clients'][key],'client_type':'confidential','grant_types':['authorization_code','refresh_token'],'redirect_uris':[RedirectURI(RedirectURIMatchingMode.STRICT,uri) for uri in DATA['redirects'][key]]+([RedirectURI(RedirectURIMatchingMode.STRICT,DATA['redirects'][key][0].replace('/sso/callback','/logged-out'),RedirectURIType.LOGOUT)] if key in ('meta','files') else []),'sub_mode':'user_uuid','issuer_mode':'per_provider','signing_key':certificate,'include_claims_in_id_token':True,'access_code_validity':'minutes=2','access_token_validity':'minutes=15','refresh_token_validity':'days=30'})
  provider.property_mappings.set([openid,profile,mapping])
  app,_=Application.objects.update_or_create(slug=key,defaults={'name':{'meta':'零号庭院','files':'文件浏览','immich':'Immich 相册'}[key],'provider':provider,'policy_engine_mode':'all'})
  PolicyBinding.objects.update_or_create(target=app,order=0,defaults={'policy':allow,'enabled':True,'timeout':30})
  providers[key]=provider.pk
 email,_=SAMLPropertyMapping.objects.update_or_create(name='garden-joplin-email',defaults={'saml_name':'email','friendly_name':'email','expression':'return '+repr(DATA['joplin_email'])})
 display,_=SAMLPropertyMapping.objects.update_or_create(name='garden-joplin-name',defaults={'saml_name':'displayName','friendly_name':'displayName','expression':"return request.user.name"})
 saml,_=SAMLProvider.objects.update_or_create(name='garden-joplin',defaults={'authentication_flow':flow,'authorization_flow':authorization,'invalidation_flow':invalidation,'acs_url':DATA['joplin_url']+'/api/saml','audience':'Joplin','sp_binding':'post','name_id_mapping':email,'signing_kp':certificate,'sign_assertion':True,'sign_response':True,'digest_algorithm':'http://www.w3.org/2001/04/xmlenc#sha256','signature_algorithm':'http://www.w3.org/2001/04/xmldsig-more#rsa-sha256','default_name_id_policy':'urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress'})
 saml.property_mappings.set([email,display])
 app,_=Application.objects.update_or_create(slug='joplin',defaults={'name':'Joplin 笔记','provider':saml,'policy_engine_mode':'all'})
 PolicyBinding.objects.update_or_create(target=app,order=0,defaults={'policy':allow,'enabled':True,'timeout':30})
 Brand.objects.filter(default=True).update(branding_title='零号庭院',flow_authentication=flow,flow_recovery=None)
 print('GARDEN_SSO_RESULT='+json.dumps({'owner_uuid':str(owner.uuid),'owner_pk':owner.pk,'oauth_providers':providers,'saml_provider':saml.pk}))
