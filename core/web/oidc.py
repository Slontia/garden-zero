"""Confidential OIDC code flow: PKCE, browser binding, signed ID tokens and one owner."""
import base64,hashlib,hmac,json,secrets,threading,time,urllib.parse,urllib.request
import jwt

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,request,fp,code,msg,headers,newurl):return None

class OIDC:
 def __init__(self,config,mode):
  self.config=config;self.mode=mode;self.pending={};self.guard=threading.Lock();self.discovery_cache=None;self.keys_cache=None
  for key in ('issuer','redirect_uri'):
   url=urllib.parse.urlsplit(config[key])
   if url.scheme!='https' or not url.netloc or url.username or url.password:raise ValueError('OIDC requires an explicit HTTPS origin')
  if not config.get('owner_sub') or not config.get('client_secret'):raise ValueError('OIDC single-owner configuration incomplete')
 def endpoint(self,url):
  expected=urllib.parse.urlsplit(self.config['issuer']);parsed=urllib.parse.urlsplit(url)
  if parsed.scheme!='https' or parsed.netloc!=expected.netloc or not parsed.path.startswith('/auth/'):raise ValueError('Untrusted identity endpoint')
  return url
 def json_request(self,url,data=None,headers=None):
  self.endpoint(url)
  opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect());request=urllib.request.Request(url,data=data,headers=headers or {})
  with opener.open(request,timeout=15) as response:body=response.read(1024*1024+1)
  if len(body)>1024*1024:raise ValueError('Oversized identity response')
  return json.loads(body)
 def discovery(self):
  if self.discovery_cache and self.discovery_cache[0]>time.time():return self.discovery_cache[1]
  value=self.json_request(self.config['issuer'].rstrip('/')+'/.well-known/openid-configuration')
  if value.get('issuer')!=self.config['issuer']:raise ValueError('Identity issuer mismatch')
  for key in ('authorization_endpoint','token_endpoint','jwks_uri'):self.endpoint(value[key])
  if value.get('end_session_endpoint'):self.endpoint(value['end_session_endpoint'])
  self.discovery_cache=(time.time()+600,value);return value
 def start(self,next_path='/'):
  if not isinstance(next_path,str) or not next_path.startswith('/') or next_path.startswith('//') or '\\' in next_path or any(ord(ch)<32 for ch in next_path):next_path='/'
  state=secrets.token_urlsafe(32);browser=secrets.token_urlsafe(32);nonce=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48)
  with self.guard:
   self.pending={key:value for key,value in self.pending.items() if value['expires']>time.time()}
   if len(self.pending)>=128:raise RuntimeError('Too many pending logins')
   self.pending[state]={'browser':hashlib.sha256(browser.encode()).hexdigest(),'nonce':nonce,'verifier':verifier,'next':next_path,'expires':time.time()+300}
  challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
  parameters={'client_id':self.config['client_id'],'redirect_uri':self.config['redirect_uri'],'response_type':'code','scope':'openid profile email','state':state,'nonce':nonce,'code_challenge':challenge,'code_challenge_method':'S256'}
  return self.discovery()['authorization_endpoint']+'?'+urllib.parse.urlencode(parameters),browser
 def validate_token(self,token,nonce):
  header=jwt.get_unverified_header(token)
  if header.get('alg')!='RS256' or not header.get('kid'):raise ValueError('Unsupported identity signature')
  keys=self.json_request(self.discovery()['jwks_uri'])
  found=[key for key in keys.get('keys',[]) if key.get('kid')==header['kid'] and key.get('kty')=='RSA']
  if len(found)!=1:raise ValueError('Unknown identity signing key')
  key=jwt.PyJWK.from_dict(found[0]).key
  if key.key_size<2048:raise ValueError('Weak identity signing key')
  claims=jwt.decode(token,key,algorithms=['RS256'],audience=self.config['client_id'],issuer=self.config['issuer'],leeway=30,options={'require':['exp','iat','sub','iss','aud','nonce']})
  if not hmac.compare_digest(str(claims['nonce']),nonce):raise ValueError('Identity nonce mismatch')
  if isinstance(claims['aud'],list) and len(claims['aud'])>1 and claims.get('azp')!=self.config['client_id']:raise ValueError('Ambiguous identity audience')
  if claims['sub']!=self.config['owner_sub']:raise PermissionError('This identity is not the configured owner')
  return claims
 def finish(self,query,browser):
  values=urllib.parse.parse_qs(query)
  if any(len(values.get(key,[]))!=1 for key in ('state','code')):raise ValueError('Incomplete identity response')
  state=values['state'][0]
  with self.guard:
   item=self.pending.get(state)
   if not item or item['expires']<=time.time() or not hmac.compare_digest(item['browser'],hashlib.sha256(browser.encode()).hexdigest()):raise ValueError('Expired or unbound login response')
   self.pending.pop(state)
  credentials=base64.b64encode((self.config['client_id']+':'+self.config['client_secret']).encode()).decode()
  data=urllib.parse.urlencode({'grant_type':'authorization_code','code':values['code'][0],'redirect_uri':self.config['redirect_uri'],'code_verifier':item['verifier']}).encode()
  response=self.json_request(self.discovery()['token_endpoint'],data,{'Content-Type':'application/x-www-form-urlencoded','Authorization':'Basic '+credentials})
  claims=self.validate_token(response['id_token'],item['nonce'])
  return {'next':item['next'],'sub':claims['sub']}
 def logout_url(self):
  # Run the identity logout flow explicitly so an unfinished application flow
  # cannot turn RP logout into a blank response. Never put ID tokens in URLs.
  origin=urllib.parse.urlsplit(self.config['issuer'])
  endpoint=origin.scheme+'://'+origin.netloc+'/auth/if/flow/default-invalidation-flow/'
  return self.endpoint(endpoint)+'?'+urllib.parse.urlencode({'next':'/logged-out'})
