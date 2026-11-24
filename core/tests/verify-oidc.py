#!/usr/bin/env python3
"""Isolated OIDC adversarial checks; no NAS credentials or production network."""
import sys,json,time,unittest,urllib.parse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'web'))
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from oidc import OIDC
class Tests(unittest.TestCase):
 def setUp(self):
  self.key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
  self.jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()));self.jwk['kid']='test'
  self.config={'issuer':'https://nas.test/auth/application/o/meta/','redirect_uri':'https://nas.test/sso/callback','owner_sub':'owner-id','client_id':'garden-meta','client_secret':'private-test-secret'}
  self.client=OIDC(self.config,'meta');self.client.json_request=self.response
  self.claims={'iss':self.config['issuer'],'aud':'garden-meta','sub':'owner-id','iat':int(time.time()),'exp':int(time.time()+120),'nonce':'nonce'}
 def response(self,url,data=None,headers=None):
  if url.endswith('openid-configuration'):return {'issuer':self.config['issuer'],'authorization_endpoint':'https://nas.test/auth/authorize/','token_endpoint':'https://nas.test/auth/token/','jwks_uri':'https://nas.test/auth/jwks/'}
  if url.endswith('jwks/'):return {'keys':[self.jwk]}
  if url.endswith('token/'):
   self.assertIn('Basic ',headers['Authorization']);self.assertIn('code_verifier=',data.decode());return {'id_token':self.token()}
  self.fail('Unexpected endpoint')
 def token(self):return jwt.encode(self.claims,self.key,algorithm='RS256',headers={'kid':'test'})
 def test_signed_owner(self):self.assertEqual(self.client.validate_token(self.token(),'nonce')['sub'],'owner-id')
 def test_bad_claims(self):
  for field,value in [('iss','https://evil.test/'),('aud','other'),('sub','other-owner'),('nonce','wrong'),('exp',int(time.time()-120))]:
   with self.subTest(field=field):
    old=self.claims[field];self.claims[field]=value
    with self.assertRaises(Exception):self.client.validate_token(self.token(),'nonce')
    self.claims[field]=old
 def test_wrong_signature(self):
  token=jwt.encode(self.claims,rsa.generate_private_key(public_exponent=65537,key_size=2048),algorithm='RS256',headers={'kid':'test'})
  with self.assertRaises(Exception):self.client.validate_token(token,'nonce')
 def test_unsigned_and_symmetric(self):
  for algorithm,key in [('none',None),('HS256','a'*32)]:
   with self.assertRaises(Exception):self.client.validate_token(jwt.encode(self.claims,key,algorithm=algorithm,headers={'kid':'test'}),'nonce')
 def test_browser_binding_and_replay(self):
  location,browser=self.client.start('/apps');args=urllib.parse.parse_qs(urllib.parse.urlsplit(location).query);state=args['state'][0]
  self.assertEqual(args['code_challenge_method'],['S256']);self.assertNotIn('client_secret',args)
  self.claims['nonce']=args['nonce'][0];query=urllib.parse.urlencode({'state':state,'code':'once'})
  with self.assertRaises(Exception):self.client.finish(query,'wrong-browser')
  self.assertEqual(self.client.finish(query,browser)['next'],'/apps')
  with self.assertRaises(Exception):self.client.finish(query,browser)
 def test_redirect_and_expiry(self):
  for path in ['https://evil.test','//evil.test','/\\evil','/\nevil']:
   location,browser=self.client.start(path);state=urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)['state'][0];self.assertEqual(self.client.pending[state]['next'],'/')
  self.client.pending[state]['expires']=time.time()-1
  with self.assertRaises(Exception):self.client.finish(urllib.parse.urlencode({'state':state,'code':'once'}),browser)
 def test_untrusted_discovery_endpoint(self):
  self.client.json_request=lambda *args,**kwargs:{'issuer':self.config['issuer'],'authorization_endpoint':'https://evil.test/auth/','token_endpoint':'https://nas.test/auth/token/','jwks_uri':'https://nas.test/auth/keys/'}
  with self.assertRaises(ValueError):self.client.start('/')
 def test_logout_contains_no_identity_token(self):
  url=self.client.logout_url();self.assertTrue(url.startswith('https://nas.test/auth/if/flow/default-invalidation-flow/'));self.assertNotIn('id_token',url);self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['next'],['/logged-out'])
 def test_endpoint_origin(self):
  real=OIDC(self.config,'meta')
  for url in ['http://nas.test/auth/','https://evil.test/auth/','https://nas.test/untrusted/']:
   with self.assertRaises(ValueError):real.json_request(url)
if __name__=='__main__':unittest.main()
