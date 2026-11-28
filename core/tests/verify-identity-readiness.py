#!/usr/bin/env python3
"""No containers or certificates changed: delayed-network certificate prerequisites."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import json,subprocess,unittest
from unittest.mock import patch
import nas_sso as sso

class Tests(unittest.TestCase):
 def status(self,state,name='fixture.ts.net.'):
  return subprocess.CompletedProcess([],0,json.dumps({'BackendState':state,'Self':{'DNSName':name}}),'')
 def test_waits_for_daemon_and_network(self):
  with patch.object(sso.subprocess,'run',side_effect=[subprocess.CompletedProcess([],1,'','not ready'),self.status('Starting'),self.status('Running')]) as run,patch.object(sso.time,'sleep') as sleep:
   sso.wait_network_identity('fixture.ts.net');self.assertEqual(run.call_count,3);self.assertEqual(sleep.call_count,2)
 def test_socket_only_does_not_count_as_network_ready(self):
  with patch.object(sso.subprocess,'run',return_value=self.status('Starting')),patch.object(sso.time,'sleep'),patch.object(sso.time,'monotonic',side_effect=[0,0,2]):
   with self.assertRaisesRegex(RuntimeError,'did not become ready'):sso.wait_network_identity('fixture.ts.net',timeout=1)
 def test_wrong_identity_is_refused(self):
  with patch.object(sso.subprocess,'run',return_value=self.status('Running','other.ts.net.')),patch.object(sso.time,'sleep'),patch.object(sso.time,'monotonic',side_effect=[0,0,2]):
   with self.assertRaises(RuntimeError):sso.wait_network_identity('fixture.ts.net',timeout=1)
 def test_authorization_needed_is_actionable(self):
  with patch.object(sso.subprocess,'run',return_value=self.status('NeedsLogin')):
   with self.assertRaisesRegex(RuntimeError,'authorization'):sso.wait_network_identity('fixture.ts.net')

if __name__=='__main__':unittest.main()
