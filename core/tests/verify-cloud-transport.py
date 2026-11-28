#!/usr/bin/env python3
"""Opt-in real transport test: private Local driver + random fixtures, no cloud account/data."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import argparse, json, os, secrets, shutil, tempfile
import nas_continuous as continuous
from nasctl import cfg, lock
import nas_cloud as cloud

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--run', action='store_true'); args = parser.parse_args()
    if not args.run: raise SystemExit('Read source, then pass --run for isolated fixture writes to the private gateway')
    c = cfg(); host = Path(c['host']); stamp = secrets.token_hex(6)
    work = host / 'runtime' / ('cloud-transport-' + stamp); work.mkdir(mode=0o700)
    for name in ('config', 'state', 'secrets', 'backups'): (work / name).mkdir()
    shutil.copyfile(host / 'secrets/cloud-bridge-token', work / 'secrets/cloud-bridge-token')
    shutil.copyfile(host / 'config/versions.json', work / 'config/versions.json')
    (work / 'secrets/backup-password').write_text(secrets.token_urlsafe(32))
    fixture = {**c, 'host': str(work), 'backup_root': str(work / 'backups'), 'data': str(work/'data')}
    (work/'data').mkdir()
    def capture(stamp):
        root=continuous.roots(fixture);stage=root/('.incomplete-'+stamp);stage.mkdir()
        builder=continuous.Builder(fixture,stage);builder.prepare(final=True)
        for name in ('database.dump','configuration.tar'):(stage/name).write_text('isolated fake recovery payload')
        m={'schema':4,'time':stamp,'databases':{'fixture':{'dump':'database.dump'}},'content':builder.entries,'files':{n:continuous.digest_file(stage/n) for n in ('database.dump','configuration.tar')}}
        (stage/'manifest.json').write_text(json.dumps(m));(stage/'COMPLETE').write_text(stamp+'\n')
        dest=root/stamp;stage.rename(dest)
        cloud.save(work/'state/cloud-snapshot.json',{'state':'success','path':str(dest),'last_success':stamp})
        continuous.validate(fixture,dest);return dest
    local = host / 'state/cloud-gateway' / ('acceptance-' + stamp); local.mkdir(); os.chown(local, 1001, 1001)
    mount = '/acceptance-' + stamp; ident = 'fixture-' + stamp
    storage = None; checks = []; complete = False
    try:
        storage = cloud.api(c, '/api/admin/storage/create', {'mount_path': mount, 'driver': 'Local', 'addition': json.dumps({'root_folder_path': '/opt/openlist/data/' + local.name}), 'cache_expiration': 0, 'webdav_policy': 'native_proxy'})
        cloud.save(work / 'state/cloud-accounts.json', {'schema': 1, 'accounts': [{'id': ident, 'provider': 'aliyun', 'name': 'Isolated transport fixture', 'mount': mount, 'remote_path': 'GardenZero-Backup', 'state': 'authorized'}]})
        target = local / 'GardenZero-Backup'; target.mkdir(); os.chown(target, 1001, 1001)
        unknown = target / 'unknown.txt'; unknown.write_text('preserve fixture'); os.chown(unknown, 1001, 1001)
        try: cloud.verify(fixture, ident)
        except RuntimeError as error:
            if '不是空目录' not in str(error): raise
        else: raise RuntimeError('Unknown target was adopted')
        assert unknown.read_text() == 'preserve fixture'; checks.append('unknown nonempty destination refused without modifying files'); unknown.unlink()
        cloud.verify(fixture, ident); checks.append('real WebDAV upload/download SHA-256 roundtrip and restic init/check')
        (work/'data/removed.txt').write_text('old-version-' + stamp);(work/'data/kept.txt').write_text('keep-' + stamp)
        first=capture('20261124-000001')
        initial = cloud.backup(fixture, first)['accounts'][0]['snapshot_id']; checks.append('first encrypted snapshot committed and checked')
        repo = 'rclone:' + ident + ':repository'
        try: cloud.command(fixture, ['restic', '-r', repo, 'backup', '--files-from', '/missing-fixture-file'], source=first)
        except RuntimeError: pass
        else: raise RuntimeError('Intentional incomplete backup unexpectedly passed')
        assert cloud.command(fixture, ['restic', '-r', repo, 'dump', initial, '/backup-set/data/removed.txt']) == 'old-version-' + stamp
        checks.append('failed backup retains a readable previous complete snapshot')
        (work/'data/removed.txt').unlink();(work/'data/new.txt').write_text('new-version-'+stamp)
        second=capture('20261124-000002')
        latest = cloud.backup(fixture, second)['accounts'][0]['snapshot_id']
        listing = cloud.command(fixture, ['restic', '-r', repo, 'ls', '--json', latest])
        paths = [json.loads(line).get('path') for line in listing.splitlines() if line.startswith('{')]
        assert '/backup-set/data/removed.txt' not in paths and '/backup-set/data/new.txt' in paths
        assert cloud.command(fixture, ['restic', '-r', repo, 'dump', initial, '/backup-set/data/removed.txt']) == 'old-version-' + stamp
        assert cloud.command(fixture, ['restic', '-r', repo, 'dump', latest, '/backup-set/data/new.txt']) == 'new-version-' + stamp
        checks.append('directory recovery points: latest view removes deleted file; old snapshot still restores it')
        assert not (second/'user-content.tar').exists()
        assert cloud.command(fixture,['restic','-r',repo,'dump',latest,'/backup-set/database.dump'])=='isolated fake recovery payload'
        checks.append('directory layout restores database export beside data without full user-content tar')
        try: cloud.command(fixture, ['sh', '-c', 'touch /backup-set/should-not-write'], source=second)
        except RuntimeError: pass
        else: raise RuntimeError('Read-only fixture mount accepted a write')
        assert not (second / 'should-not-write').exists(); checks.append('actual backup source mount rejects writes')
        try: cloud.command(fixture, ['sh', '-c', 'printf wrong >/tmp/wrong; export RESTIC_PASSWORD_FILE=/tmp/wrong; exec restic -r "$1" snapshots', 'sh', repo])
        except RuntimeError: pass
        else: raise RuntimeError('Wrong encryption password unlocked repository')
        checks.append('remote repository cannot be opened with the wrong encryption password')
        cloud.command(fixture, ['restic', '-r', repo, 'check', '--read-data']); checks.append('full encrypted repository data verification')
        complete = True
    finally:
        if complete:
            users = cloud.api(c, '/api/admin/user/list?page=1&per_page=100')['content']
            own = next(x for x in users if x['username'] == ident)
            cloud.api(c, '/api/admin/user/delete?id=' + str(own['id']), {})
            cloud.api(c, '/api/admin/storage/delete?id=' + str(storage['id']), {})
            # Only this test's newly-created random fixture tree; never the gateway or data root.
            assert local.name == 'acceptance-' + stamp and local.parent == host / 'state/cloud-gateway'
            shutil.rmtree(local)
        else: print('Fixture retained for diagnosis:', work, flush=True)
    report = {'state': 'PASS', 'checks': checks, 'scope': 'Local gateway transport only; no real Aliyun account acceptance', 'test_storage_removed': True}
    (work / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print('PASS:', len(checks), 'transport checks;', work / 'result.json')

if __name__ == '__main__':
    with lock(): main()
