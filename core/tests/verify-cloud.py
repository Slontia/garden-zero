#!/usr/bin/env python3
"""Cloud authorization, secret boundaries and one-way backup safety without real accounts."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import http.cookiejar, json, subprocess, tempfile, time, unittest, threading
from unittest.mock import patch
import nas_cloud as cloud

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.host = Path(self.tmp.name)
        for name in ('state', 'secrets', 'config', 'backups'): (self.host / name).mkdir()
        self.c = {'host': str(self.host), 'project': 'nas-fixture', 'backup_root': str(self.host / 'backups'), 'cloud_bridge_prepared': True}
        self.account = {'id': 'aliyun-fixture', 'provider': 'aliyun', 'name': 'Fixture', 'mount': '/fixture', 'remote_path': 'GardenZero-Backup', 'state': 'authorized', 'refresh_token': 'must-stay-private'}
        self.write_accounts()

    def write_accounts(self):
        cloud.save(self.host / 'state/cloud-accounts.json', {'schema': 1, 'accounts': [self.account]})

    def test_summary_uses_allowlist(self):
        result = cloud.summary(self.c)
        self.assertNotIn('must-stay-private', json.dumps(result)); self.assertNotIn('mount', result[0])

    def test_empty_cookie_jar_is_retained(self):
        jar = http.cookiejar.CookieJar(); opener = cloud.broker(jar)
        self.assertIs(next(h.cookiejar for h in opener.handlers if hasattr(h, 'cookiejar')), jar)

    def test_auth_rejects_unknown_provider_before_network(self):
        with patch.object(cloud, 'request') as req:
            with self.assertRaises(RuntimeError): cloud.authorize_start(self.c, '115', 'Fixture')
            req.assert_not_called()

    def test_qr_disallows_untrusted_url(self):
        with patch.object(cloud, 'request', return_value={'text': 'https://evil.invalid/qr', 'sid': 'sid'}):
            with self.assertRaises(RuntimeError): cloud.authorize_start(self.c, 'aliyun', 'Fixture')
        self.assertFalse((self.host / 'secrets/cloud-authorization.json').exists())

    def test_browser_never_receives_sid_or_cookies(self):
        with patch.object(cloud, 'request', return_value={'text': 'https://openapi.alipan.com/oauth/authorize?sid=fixture', 'sid': 'fixture'}):
            result = cloud.authorize_start(self.c, 'aliyun', 'Fixture')
        self.assertNotIn('cookies', result); self.assertNotIn('sid', result)
        self.assertEqual(cloud.authorization(self.c, result['session'])['sid'], 'fixture')
        with self.assertRaises(RuntimeError): cloud.authorization(self.c, 'wrong-session')

    def test_expired_session_destroys_private_authorization(self):
        path = self.host / 'secrets/cloud-authorization.json'
        cloud.save(path, {'session': 'fixture', 'expires': time.time() - 1})
        with self.assertRaises(RuntimeError): cloud.authorization(self.c, 'fixture')
        self.assertFalse(path.exists())

    def test_network_failure_redacts_credentials(self):
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = RuntimeError('https://secret.invalid/?refresh_token=private')
            with self.assertRaises(RuntimeError) as error: cloud.request('https://secret.invalid/')
        self.assertNotIn('private', str(error.exception)); self.assertNotIn('secret.invalid', str(error.exception))

    def test_unknown_nonempty_destination_refuses_before_writes(self):
        with patch.object(cloud, 'remote_listing', side_effect=[[{'name': 'GardenZero-Backup', 'is_dir': True}], [{'name': 'personal.txt'}]]), patch.object(cloud, 'api') as api, patch.object(cloud, 'command') as command:
            with self.assertRaisesRegex(RuntimeError, '不是空目录'): cloud.verify(self.c, self.account['id'])
            api.assert_not_called(); command.assert_not_called()
        self.assertEqual(cloud.metadata(self.c)['accounts'][0]['state'], 'failed')

    def test_worker_only_mounts_readonly_source_and_credentials(self):
        cloud.save(self.host / 'config/versions.json', {'CLOUD_BACKUP_IMAGE': {'image': 'sha256:fixture'}})
        source = self.host / 'backups/20261124-000000'
        with patch.object(cloud.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '{}', '')) as run:
            cloud.command(self.c, ['restic', 'backup', '/backup-set'], source=source)
        args = run.call_args.args[0]
        mounts = [args[i + 1] for i, v in enumerate(args) if v == '--mount']
        self.assertTrue(all(m.endswith(',readonly') for m in mounts)); self.assertEqual(len(mounts), 3)
        self.assertNotIn('/var/run/docker.sock', ' '.join(mounts)); self.assertIn('HTTPS_PROXY=', args)

    def test_incomplete_source_cannot_start_worker(self):
        with patch.object(cloud, 'command') as command:
            with self.assertRaises(RuntimeError): cloud.backup(self.c, self.host / 'backups/incomplete')
            command.assert_not_called()

    def test_unverified_account_cannot_backup(self):
        source = self.host / 'backups/20261124-000000'; source.mkdir(); (source / 'COMPLETE').write_text('fixture')
        with patch.object(cloud, 'command') as command:
            with self.assertRaisesRegex(RuntimeError, '连接验证'): cloud.backup(self.c, source)
            command.assert_not_called()

    def test_backup_without_remote_commit_cannot_report_success(self):
        self.account['state'] = 'ready'; self.write_accounts()
        source = self.host / 'backups/20261124-000000'; source.mkdir(); (source / 'COMPLETE').write_text('fixture')
        with patch.object(cloud, 'command', return_value='{"message_type":"status"}'):
            with self.assertRaisesRegex(RuntimeError, '部分网盘'): cloud.backup(self.c, source)
        self.assertIn('完整快照',cloud.summary(self.c)[0]['backup']['error'])

    def test_account_operations_are_serialized(self):
        with cloud.account_lock(self.c):
            with self.assertRaises(RuntimeError):
                with cloud.account_lock(self.c): pass

    def test_retry_requires_current_recovery_configuration(self):
        self.account.update(state='ready', verified_at='2000-01-01T00:00:00+00:00'); self.write_accounts()
        source = self.host / 'backups/20261124-000000'; source.mkdir(); (source / 'COMPLETE').write_text('fixture')
        snapshot = {'state': 'success', 'last_success': source.name}
        self.assertTrue(cloud.retry_due(self.c, snapshot, {'state': 'failed'}))
        self.assertFalse(cloud.retry_due(self.c, snapshot, {'state': 'success', 'source': source.name}))
        self.account['verified_at'] = '2099-01-01T00:00:00+00:00'; self.write_accounts()
        self.assertFalse(cloud.retry_due(self.c, snapshot, {'state': 'failed'}))

    def test_successful_authorization_starts_verification_on_server(self):
        with patch.object(cloud,'authorization',return_value={'sid':'fixture','cookies':[]}),patch.object(cloud,'request',side_effect=[{'status':'LoginSuccess'},{'refresh_token':'private'}]),patch.object(cloud,'attach',return_value={'id':self.account['id']}),patch.object(cloud,'start_verification') as start:
            result=cloud.authorize_poll(self.c,'session')
        start.assert_called_once_with(self.c,self.account['id'])
        self.assertEqual(result['verification_state'],'verifying')
        self.assertNotIn('private',json.dumps(result))

    def test_background_verification_is_persisted_and_refreshable(self):
        import nasctl
        entered=threading.Event();release=threading.Event();finished=threading.Event()
        def verify(c,ident):
            entered.set();release.wait(5)
            value=cloud.metadata(c);value['accounts'][0]['state']='ready';cloud.save(self.host/'state/cloud-accounts.json',value);finished.set()
        with patch.object(nasctl,'LOCK_ROOT',self.host/'state'),patch.object(cloud,'verify',side_effect=verify):
            try:
                with nasctl.lock():
                    result=cloud.action(self.c,{'action':'cloud-verify','account':self.account['id']})
                    self.assertEqual(result['state'],'verifying')
                    self.assertEqual(cloud.summary(self.c)[0]['state'],'verifying')
                self.assertTrue(entered.wait(2))
                self.assertEqual(cloud.summary(self.c)[0]['state'],'verifying')
            finally:release.set();finished.wait(2)
        self.assertEqual(cloud.summary(self.c)[0]['state'],'ready')

    def test_dead_verifier_is_not_displayed_as_running(self):
        self.account.update(state='verifying',verification={'pid':999999999,'process_start':'missing'})
        self.write_accounts()
        self.assertEqual(cloud.summary(self.c)[0]['state'],'failed')

    def test_profile_allowlist_excludes_provider_credentials(self):
        self.account['profile']={'nick_name':'Fixture nickname','user_id':'test-user','access_token':'private'};self.write_accounts()
        row=cloud.summary(self.c)[0]
        self.assertEqual(row['profile']['nick_name'],'Fixture nickname')
        self.assertNotIn('private',json.dumps(row))

    def test_auto_flag_is_persisted_and_manual_target_is_independent(self):
        self.account['state']='ready';self.write_accounts()
        cloud.action(self.c,{'action':'cloud-auto','account':self.account['id'],'enabled':False})
        self.assertFalse(cloud.summary(self.c)[0]['auto_backup'])
        self.assertEqual(cloud.automatic_accounts(self.c),[])
        self.assertEqual(cloud.targets(self.c,[self.account['id']])[0]['id'],self.account['id'])
        self.assertFalse(cloud.automatic_enabled(self.c))

    def test_delete_requires_explicit_confirmation_before_any_api_call(self):
        with patch.object(cloud,'api') as api:
            with self.assertRaisesRegex(RuntimeError,'二次确认'):cloud.action(self.c,{'action':'cloud-delete','account':self.account['id']})
            api.assert_not_called()
        self.assertEqual(len(cloud.summary(self.c)),1)

    def test_delete_only_removes_connection_not_remote_files(self):
        self.account['storage_id']=123;self.write_accounts()
        with patch.object(cloud,'api',side_effect=[{'content':[{'id':42,'username':self.account['id']}]},None,None]) as api:
            cloud.action(self.c,{'action':'cloud-delete','account':self.account['id'],'confirmed':True})
        self.assertTrue(all(call.args[1].startswith('/api/admin/') for call in api.call_args_list))
        self.assertFalse(any('/api/fs/' in call.args[1] for call in api.call_args_list))
        self.assertEqual(cloud.summary(self.c),[])
        archive=(self.host/'state/cloud-removed-accounts.json').read_text()
        self.assertNotIn('must-stay-private',archive)

    def test_backup_failure_does_not_stop_other_targets(self):
        self.account['state']='ready'
        other={**self.account,'id':'aliyun-second'}
        cloud.save(self.host/'state/cloud-accounts.json',{'schema':1,'accounts':[self.account,other]})
        source=self.host/'backups/20261125-000000';source.mkdir();(source/'COMPLETE').write_text('fixture')
        def command(c,args,**kw):
            if args[3]=='backup':
                if self.account['id'] in args[2]:raise RuntimeError('fixture target failure')
                return '{"message_type":"summary","snapshot_id":"fixture-snapshot"}'
            return ''
        with patch.object(cloud,'command',side_effect=command):
            with self.assertRaises(RuntimeError):cloud.backup(self.c,source)
        rows=cloud.summary(self.c)
        self.assertEqual(rows[0]['backup']['state'],'failed')
        self.assertEqual(rows[1]['backup']['state'],'success')
        self.assertTrue(rows[1]['backup']['last_success'])

if __name__ == '__main__': unittest.main(verbosity=2)
