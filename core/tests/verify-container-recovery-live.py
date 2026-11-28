#!/usr/bin/env python3
"""Opt-in crash/recovery of one isolated dummy container. Never crash a NAS business service."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import argparse, datetime, json, secrets, subprocess, threading, time
from unittest.mock import patch
import nasctl as n
import nas_recovery as recovery


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--run', action='store_true'); args = parser.parse_args()
    if not args.run: raise SystemExit('Read source, then pass --run to create/crash/remove one isolated dummy container')
    real = n.cfg(); host = Path(real['host'])
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(3)
    work = host / 'runtime' / ('container-recovery-' + stamp); work.mkdir(mode=0o700)
    for directory in ('config', 'state', 'data', 'locks', 'logs'): (work / directory).mkdir()
    c = {**real, 'host': str(work), 'data': str(work / 'data'), 'project': 'nas-recovery-check-' + secrets.token_hex(4)}
    image = json.loads((host / 'config/versions.json').read_text())['RESTIC_IMAGE']['image']
    definition = {'services': {'git': {'image': image, 'entrypoint': ['/bin/sh', '-c', 'exec sleep 600'], 'network_mode': 'none', 'restart': 'no', 'read_only': True, 'cap_drop': ['ALL'], 'mem_limit': '32m', 'labels': {'nas.purpose': 'recovery-validation'}, 'healthcheck': {'test': ['CMD', 'true'], 'interval': '1s', 'timeout': '1s', 'retries': 3}}}}
    (work / 'config/nas.yml').write_text(json.dumps(definition)); (work / 'config/compose.env').write_text('# isolated fixture\n')
    (work / 'state/recovery-control.json').write_text('{"enabled":true}')
    before = (host / 'state/desired-modules.json').read_bytes()
    original_ids = subprocess.check_output(n.DOCKER + ['ps', '-q', '--filter', 'label=com.docker.compose.project=' + real['project']], text=True).split()
    original_starts = subprocess.check_output(n.DOCKER + ['inspect', '--format', '{{.Id}} {{.State.StartedAt}}', *original_ids], text=True)
    name = None; checks = []
    stopped = threading.Event(); died = threading.Event(); events = recovery.queue.Queue(maxsize=1)
    original_relevant = recovery.relevant_event
    def observe(config, event):
        relevant = original_relevant(config, event)
        if relevant and event.get("Action") == "die": died.set()
        return relevant
    try:
        n.compose(c, 'up', '-d', '--no-build', '--wait', '--wait-timeout', '30', 'git')
        row = n.inventory(c)[0]; name = row['name']
        info = json.loads(subprocess.check_output(n.DOCKER + ['inspect', name], text=True))[0]
        assert info['Config']['Labels']['nas.purpose'] == 'recovery-validation'
        assert not info['Mounts'] and not info['HostConfig']['PortBindings']
        with patch.object(recovery, 'relevant_event', side_effect=observe):
            watcher = threading.Thread(target=recovery.watch_events, args=(c, events, stopped), daemon=True)
            watcher.start()
            events.get(timeout=10)  # Listener opened; --since closes subscription startup races.
            subprocess.run(n.DOCKER + ['kill', '--signal', 'KILL', name], check=True, stdout=subprocess.DEVNULL)
            assert died.wait(15), 'Native Docker die event was not received'
            checks.append('native Docker event stream receives isolated container die event')
        assert n.inventory(c)[0]['state'] == 'exited'; checks.append('isolated dummy exit is detected')
        with patch.object(n, 'LOCK_ROOT', work / 'locks'), patch.object(n, 'desired_state', return_value={'modules': ['git']}), patch.object(recovery, 'check_storage', return_value=None):
            recovery.tick(c)
            assert n.inventory(c)[0]['health'] == 'healthy'; checks.append('actual Compose recovery returns dummy container to healthy')
            recovery.pause(c)
            subprocess.run(n.DOCKER + ['stop', '--time', '2', name], check=True, stdout=subprocess.DEVNULL)
            assert recovery.tick(c)['state'] == 'paused'
            assert n.inventory(c)[0]['state'] == 'exited'; checks.append('explicitly paused NAS session stays stopped')
        assert (host / 'state/desired-modules.json').read_bytes() == before
        assert subprocess.check_output(n.DOCKER + ['inspect', '--format', '{{.Id}} {{.State.StartedAt}}', *original_ids], text=True) == original_starts
        checks.append('production choices and running container start times are unchanged')
    finally:
        stopped.set()
        if name:
            info = json.loads(subprocess.check_output(n.DOCKER + ['inspect', name], text=True))[0]
            assert info['Config']['Labels']['nas.purpose'] == 'recovery-validation'
            if info['State']['Running']: subprocess.run(n.DOCKER + ['stop', '--time', '2', name], check=True, stdout=subprocess.DEVNULL)
            subprocess.run(n.DOCKER + ['rm', name], check=True, stdout=subprocess.DEVNULL)
    (work / 'result.json').write_text(json.dumps({'state': 'PASS', 'checks': checks, 'scope': 'isolated dummy only; safety policy covered separately'}, indent=2))
    print('PASS:', len(checks), 'isolated real Docker recovery checks;', work / 'result.json')

if __name__ == '__main__': main()
