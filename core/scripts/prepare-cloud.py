#!/usr/bin/env python3
"""Prepare only private NAS cloud gateway state; no account authorization or uploads."""
import sys
sys.dont_write_bytecode = True
import argparse, json, os, re, secrets, subprocess
from pathlib import Path
from nasctl import cfg, DOCKER, lock
from nas_cloud import save

def prepare(c):
    host = Path(c['host']); state = host / 'state/cloud-gateway'; tokenpath = host / 'secrets/cloud-bridge-token'
    versions = json.loads((Path(c['root']) / 'core/templates/versions.lock.json').read_text())
    image = versions['OPENLIST_IMAGE']['image']
    if not state.exists(): state.mkdir(mode=0o700)
    if state.is_symlink(): raise RuntimeError('Linked gateway state is not supported')
    config = state / 'config.json'
    if not config.exists():
        save(config, {'log': {'enable': False}, 'scheme': {'address': '0.0.0.0', 'http_port': 5244},
            'database': {'type': 'sqlite3', 'db_file': 'data/data.db'}, 'temp_dir': 'data/temp', 'bleve_dir': 'data/bleve', 'min_free_memory': 64, 'max_block_limit': 8, 'auto_memory_limit': 256})
    current=json.loads(config.read_text())
    if current.get('dist_dir') in ('data/dist','/opt/openlist/data/dist'):
        current['dist_dir']='';save(config,current)
    os.chown(state, 1001, 1001); os.chown(config, 1001, 1001); config.chmod(0o600)
    if not tokenpath.exists() or not (host/'secrets/cloud-bridge-hardened.json').is_file():
        # CLI output contains bootstrap secrets; capture in memory, never print or log it.
        result = subprocess.run(DOCKER + ['run', '--rm', '--network', 'none', '--env-file', '/dev/stdin', '--mount', f'type=bind,source={state},target=/opt/openlist/data',
            '--entrypoint', '/opt/openlist/openlist', image, 'admin', 'token'], input='OPENLIST_ADMIN_PASSWORD='+secrets.token_urlsafe(48)+'\n', text=True, capture_output=True, timeout=60)
        match = re.search(r'Admin token:\s*(\S+)', result.stdout)
        if result.returncode or not match: raise RuntimeError('Private gateway bootstrap failed; sensitive output suppressed')
        if tokenpath.exists():
            if tokenpath.read_text().strip()!=match[1]:raise RuntimeError('Gateway token changed unexpectedly; inspect before overwriting credentials')
        else:
            fd = os.open(tokenpath, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream: stream.write(match[1] + '\n')
    if not (host / 'secrets/backup-password').is_file(): raise RuntimeError('Existing backup encryption password is required')
    c['cloud_bridge_prepared'] = True
    save(host / 'config/config.json', c)
    print(json.dumps({'state': 'prepared', 'account_authorized': False, 'uploaded': False}))

if __name__ == '__main__':
    if os.geteuid() != 0: raise SystemExit('Requires root')
    parser=argparse.ArgumentParser();parser.add_argument('--harden',action='store_true');args=parser.parse_args()
    with lock():
        if args.harden:
            from nas_cloud import harden
            harden(cfg());print('Private gateway hardening completed')
        else:prepare(cfg())
