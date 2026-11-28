#!/usr/bin/env python3
"""Isolated recovery policy tests. No production containers, disks, or services are changed."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import contextlib, json, subprocess, tempfile, unittest
from unittest.mock import patch
import nasctl as n
import nas_recovery as r
real_enabled = r.enabled

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); (self.root / 'state').mkdir(); (self.root / 'locks').mkdir()
        self.c = {'host': str(self.root), 'project': 'fixture', 'ml_enabled': True}
        self.selected = ['meta', 'tailscale', 'postgres', 'immich', 'ml']
        self.rows = {s: {'service': s, 'name': 'fixture-' + s, 'state': 'running', 'health': 'healthy', 'exit_code': 0} for services in n.MODULES.values() for s in services}
        self.specs = {s: {} for s in self.rows}
        self.specs['immich-server']['depends_on'] = {'database': {'condition': 'service_healthy'}, 'cache': {'condition': 'service_healthy'}}
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        for target, value in [('LOCK_ROOT', self.root / 'locks'), ('audit', None)]:
            self.stack.enter_context(patch.object(n, target, value) if value is not None else patch.object(n, target))
        self.stack.enter_context(patch.object(r, 'enabled', return_value=True))
        self.stack.enter_context(patch.object(n,'volume'))
        self.storage = self.stack.enter_context(patch.object(r, 'check_storage', return_value=None))
        self.stack.enter_context(patch.object(r, 'definition', return_value={'services': self.specs}))
        self.stack.enter_context(patch.object(n, 'desired_state', side_effect=lambda c: {'modules': self.selected}))
        self.stack.enter_context(patch.object(n, 'inventory', side_effect=lambda c: list(self.rows.values())))
        self.compose = self.stack.enter_context(patch.object(n, 'compose', side_effect=self.recovered))
    def recovered(self, c, *args):
        self.rows[args[-1]].update(state='running', health='healthy')
    def down(self, name): self.rows[name].update(state='exited', health='unhealthy', exit_code=1)
    def test_healthy_services_are_untouched(self):
        r.tick(self.c, 100); self.compose.assert_not_called()
    def test_only_selected_crashed_service_is_recovered(self):
        self.down('immich-server'); r.tick(self.c, 100)
        self.assertEqual(self.compose.call_args.args[-1], 'immich-server')
        self.assertIn('--no-deps', self.compose.call_args.args)
        self.assertEqual(self.compose.call_count, 1)
    def test_disabled_apps_are_not_started(self):
        self.selected = ['meta', 'tailscale', 'postgres']; self.down('immich-server'); self.down('cache'); self.down('immich-machine-learning')
        r.tick(self.c, 100); self.compose.assert_not_called()
    def test_dependency_is_recovered_before_its_consumer(self):
        self.down('database'); self.down('immich-server'); r.tick(self.c, 100)
        self.assertEqual(self.compose.call_args.args[-1], 'database')
        r.tick(self.c, 130); self.assertEqual(self.compose.call_args.args[-1], 'immich-server')
    def test_ml_waits_for_healthy_parent(self):
        self.down('immich-machine-learning'); self.rows['immich-server']['health'] = 'unhealthy'
        r.tick(self.c, 100); self.compose.assert_not_called()
    def test_paused_containers_are_not_forcibly_unpaused(self):
        self.rows['immich-server']['state'] = 'paused'; r.tick(self.c, 100); self.compose.assert_not_called()
    def test_running_unhealthy_database_is_not_killed(self):
        self.rows['database']['health'] = 'unhealthy'; r.tick(self.c, 100); self.compose.assert_not_called()
    def test_backup_lock_prevents_recovery(self):
        self.down('immich-server')
        with n.lock():
            with self.assertRaises(RuntimeError): r.tick(self.c, 100)
        self.compose.assert_not_called(); self.storage.assert_not_called()
    def test_application_operation_lock_prevents_recovery(self):
        self.down('immich-server')
        with n.lock(shared=True):
            with self.assertRaises(RuntimeError): r.tick(self.c, 100)
        self.compose.assert_not_called()
    def test_storage_block_does_not_restart_writers(self):
        self.down('immich-server'); self.storage.side_effect = RuntimeError('storage blocked')
        r.tick(self.c, 100); self.compose.assert_not_called()
    def test_management_can_recover_during_storage_block(self):
        self.down('meta'); self.down('immich-server'); self.storage.side_effect = RuntimeError('storage blocked')
        r.tick(self.c, 100); self.assertEqual(self.compose.call_args.args[-1], 'meta')
    def test_management_does_not_mount_a_wrong_data_volume(self):
        self.down('meta'); self.storage.side_effect=RuntimeError('storage blocked')
        with patch.object(n,'volume',side_effect=RuntimeError('wrong volume')):
            r.tick(self.c,100)
        self.compose.assert_not_called()
    def test_io_fault_quarantines_before_recovery(self):
        self.storage.return_value = 'loop0 I/O error'; self.down('immich-server')
        with patch.object(r, 'quarantine') as quarantine:
            result = r.tick(self.c, 100); quarantine.assert_called_once()
        self.assertEqual(result['state'], 'storage_blocked'); self.compose.assert_not_called()
    def test_retry_backoff_and_budget(self):
        self.down('immich-server'); self.compose.side_effect = RuntimeError('fixture failure')
        for now in [0, 29, 30, 149, 150, 151, 1799]: r.tick(self.c, now)
        self.assertEqual(self.compose.call_count, 3)
        state = json.loads((self.root / 'state/container-recovery.json').read_text())
        self.assertEqual(state['services']['immich-server']['state'], 'cooldown')
        r.tick(self.c, 1801); self.assertEqual(self.compose.call_count, 4)
    def test_backoff_starts_after_a_slow_failed_start(self):
        self.down('immich-server');self.compose.side_effect=RuntimeError('fixture timeout')
        with patch.object(r.time,'monotonic',side_effect=[100,220]):r.tick(self.c,100)
        value=json.loads((self.root/'state/container-recovery.json').read_text())
        self.assertEqual(value['services']['immich-server']['next_retry'],250)
        r.tick(self.c,249);self.assertEqual(self.compose.call_count,1)
    def test_manual_start_resets_only_requested_retry_budget(self):
        path = self.root / 'state/container-recovery.json'
        path.write_text(json.dumps({'services': {'immich-server': {'attempts': [1, 2, 3]}, 'database': {'attempts': [1]}}}))
        r.pause(self.c); r.manual_started(self.c, ['immich-server'])
        self.assertTrue(json.loads((self.root / 'state/recovery-control.json').read_text())['enabled'])
        self.assertEqual(set(json.loads(path.read_text())['services']), {'database'})
    def test_session_shutdown_overrides_enabled_control(self):
        r.manual_started(self.c, [])
        with patch.object(r.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'deactivating\n', '')):
            self.assertFalse(real_enabled(self.c))
        with patch.object(r.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'active\n', '')):
            self.assertTrue(real_enabled(self.c))
            r.pause(self.c); self.assertFalse(real_enabled(self.c))
    def test_kernel_fault_matching_is_device_specific(self):
        self.assertIsNone(r.storage_fault(['Buffer I/O error on device loop1'], '/dev/loop0'))
        self.assertIsNotNone(r.storage_fault(['EXT4-fs (loop0): failed to convert unwritten extents -- potential data loss!'], '/dev/loop0'))
    def test_journal_scans_oldest_unread_chunk_without_skipping(self):
        line = json.dumps({'__CURSOR': 'fixture-cursor', 'MESSAGE': 'normal'}) + '\n'
        with patch.object(r.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, line, '')) as run:
            r.journal(self.c, {'validated_at': 1})
            self.assertIn('+2000', run.call_args.args[0])
            r.journal(self.c, {'validated_at': 1})
            self.assertIn('--after-cursor', run.call_args.args[0])
    def test_quarantine_uses_graceful_database_stop(self):
        with patch.object(n, 'stop') as stop, patch.object(r.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            r.quarantine(self.c, 'test I/O error')
        self.assertTrue((self.root / 'state/deployment-blocked').exists())
        self.assertTrue(all(call.kwargs['remember_selection'] is False for call in stop.call_args_list))
        args = run.call_args.args[0]
        self.assertIn('pg_ctl', args); self.assertNotIn('kill', args); self.assertNotIn('umount', args)

    def test_recovery_loop_survives_unwritable_error_log(self):
        with patch.object(r,'tick',side_effect=RuntimeError('Docker offline')),patch.object(n,'audit',side_effect=OSError('log full')):
            self.assertEqual(r.reconcile(self.c),5)

    def test_events_are_scoped_and_ignore_temporary_jobs(self):
        event={'Type':'container','Action':'die','Actor':{'Attributes':{'com.docker.compose.project':'fixture'}}}
        self.assertTrue(r.relevant_event(self.c,event))
        event['Actor']['Attributes']['com.docker.compose.oneoff']='True'
        self.assertFalse(r.relevant_event(self.c,event))
        event['Actor']['Attributes']={'com.docker.compose.project':'another-project'}
        self.assertFalse(r.relevant_event(self.c,event))
        args=r.event_command(self.c,123)
        self.assertIn('label=com.docker.compose.project=fixture',args)
        self.assertEqual(args[args.index('--since')+1],'123')

    def test_idle_wait_is_indefinite_and_only_pending_retries_have_timers(self):
        self.assertIsNone(r.retry_delay({'services':{'git':{'state':'healthy','next_retry':100}}},90))
        self.assertEqual(r.retry_delay({'services':{'git':{'state':'retry_wait','next_retry':120}}},90),30)
        self.assertEqual(r.retry_delay({'services':{'git':{'state':'cooldown','next_retry':200}}},90),110)

    def test_busy_lifecycle_does_not_drop_the_exit_event(self):
        with patch.object(r,'tick',side_effect=RuntimeError('互斥维护')):
            self.assertEqual(r.reconcile(self.c),5)

    def test_event_bursts_coalesce_without_blocking(self):
        events=r.queue.Queue(maxsize=1)
        for _ in range(100):r.notify(events)
        self.assertEqual(events.qsize(),1)

if __name__ == '__main__': unittest.main(verbosity=2)
