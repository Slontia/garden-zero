#!/usr/bin/env python3
"""Private lock fixtures and mocked service work; never operate production containers."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import concurrent.futures,json,tempfile,threading,time,unittest
from unittest.mock import patch
import nasctl as n
import nas_apps as apps
class Tests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='nas-app-concurrency-');self.root=Path(self.temp.name);self.patch=patch.object(n,'LOCK_ROOT',self.root/'locks');self.patch.start()
 def tearDown(self):self.patch.stop();self.temp.cleanup()
 def test_independent_applications_enter_at_the_same_time(self):
  barrier=threading.Barrier(2);entered=[]
  def job(name):
   with n.application_lock(name,{'action':'app-power','enabled':True}):
    entered.append(name);barrier.wait(timeout=2)
  with concurrent.futures.ThreadPoolExecutor(2) as pool:list(pool.map(job,['git','knowledge']))
  self.assertEqual(set(entered),{'git','knowledge'});self.assertFalse(n.application_operations())
 def test_parent_and_its_feature_cannot_overlap(self):
  with n.application_lock('immich',{'action':'app-power','enabled':True}):
   with self.assertRaisesRegex(RuntimeError,'此应用'):
    with n.application_lock('immich',{'action':'app-feature','feature':'intelligence','enabled':True}):pass
 def test_maintenance_excludes_app_operations(self):
  with n.lock():
   with self.assertRaises(RuntimeError):
    with n.application_lock('git',{'action':'app-power','enabled':True}):pass
 def test_running_app_operation_excludes_backup_or_shutdown(self):
  with n.application_lock('git',{'action':'app-power','enabled':True}):
   with self.assertRaises(RuntimeError):
    with n.lock():pass
 def test_operation_metadata_visible_then_cleared(self):
  with n.application_lock('git',{'action':'app-power','enabled':True}):
   value=n.application_operations()['git'];self.assertTrue(value['enabled']);self.assertIn('started_at',value)
  self.assertFalse(n.application_operations())
 def test_error_releases_locks_and_operation_metadata(self):
  with self.assertRaises(ValueError):
   with n.application_lock('git',{'action':'app-power','enabled':True}):raise ValueError('fixture')
  self.assertFalse(n.application_operations())
  with n.lock():pass
 def test_http_action_dispatch_is_concurrent_between_apps(self):
  barrier=threading.Barrier(2);visited=[]
  def power(c,name,enabled):visited.append(name);barrier.wait(timeout=2)
  with patch.object(apps,'power',side_effect=power),concurrent.futures.ThreadPoolExecutor(2) as pool:
   futures=[pool.submit(n.action,{}, {'action':'app-power','app':name,'enabled':True}) for name in ['git','knowledge']]
   for future in futures:future.result(timeout=3)
  self.assertEqual(set(visited),{'git','joplin'})
 def test_concurrent_intent_updates_do_not_overwrite_other_app(self):
  (self.root/'state').mkdir();c={'host':str(self.root),'autostart':['meta','tailscale']};original=n.desired_state
  def slow_read(config):value=original(config);time.sleep(.03);return value
  barrier=threading.Barrier(2)
  def change(module):barrier.wait(timeout=2);n.remember(c,[module],True)
  with patch.object(n,'desired_state',side_effect=slow_read),concurrent.futures.ThreadPoolExecutor(2) as pool:list(pool.map(change,['git','knowledge']))
  self.assertTrue({'git','knowledge'}<=set(original(c)['modules']))
 def test_feature_preference_does_not_claim_effective_while_starting(self):
  modules={m:{'state':'ready','containers':[{'state':'running','health':'healthy'} for _ in services]} for m,services in n.MODULES.items()}
  modules['ml']['state']='starting'
  feature=apps.snapshot({},modules,{'ml_preference':True})['immich']['features'][0]
  self.assertTrue(feature['enabled']);self.assertTrue(feature['active']);self.assertFalse(feature['effective'])
  modules['ml']['state']='ready';self.assertTrue(apps.snapshot({},modules,{'ml_preference':True})['immich']['features'][0]['effective'])
 def test_stopped_parent_means_feature_is_not_effective(self):
  modules={m:{'state':'stopped','containers':[]} for m in n.MODULES}
  feature=apps.snapshot({},modules,{'ml_preference':True})['immich']['features'][0]
  self.assertTrue(feature['enabled']);self.assertFalse(feature['effective'])
if __name__=='__main__':unittest.main(verbosity=2)
