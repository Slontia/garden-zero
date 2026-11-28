"""Guarded reconciliation for selected NAS services, never arbitrary Docker workloads."""
import sys
sys.dont_write_bytecode = True
import datetime, json, queue, re, shutil, subprocess, threading, time
from pathlib import Path
from nas_mirror import atomic

WINDOW = 1800
BACKOFF = (30, 120, 600)
STABLE = 900
SAFE_SERVICES = {'meta', 'tailscale'}
_DEFINITION = None


def read(path, default):
    return json.loads(path.read_text()) if path.is_file() else default


def manual_started(c, services):
    state = Path(c['host']) / 'state'
    atomic(state / 'recovery-control.json', {'enabled': True})
    path = state / 'container-recovery.json'
    value = read(path, {'services': {}})
    for service in services:
        value['services'].pop(service, None)
    atomic(path, value)


def pause(c):
    atomic(Path(c['host']) / 'state/recovery-control.json', {'enabled': False})


def enabled(c):
    control = read(Path(c['host']) / 'state/recovery-control.json', None)
    if control is not None and control.get('enabled') is not True:return False
    result = subprocess.run(['systemctl', 'show', '-p', 'ActiveState', '--value', 'nas-session.service'], text=True, capture_output=True, timeout=5)
    if result.returncode != 0 or result.stdout.strip()=='deactivating':return False
    return bool(control and control.get('enabled')) or result.stdout.strip()=='active'


def record_storage_validation(c, validated_at=None):
    atomic(Path(c['host']) / 'state/storage-validation.json', {
        'image': str(Path(c['image']).resolve()), 'image_uuid': c['image_uuid'],
        'validated_at': time.time() if validated_at is None else validated_at, 'method': 'offline_readonly_fsck',
    })


def journal(c, baseline):
    path = Path(c['host']) / 'state/recovery-kernel-cursor.json'
    previous = read(path, {})
    prefix = ['journalctl', '-k', '--no-pager', '-o', 'json', '-n', '+2000']
    # --since still applies when a retained cursor was rotated out.
    args = prefix + ['--since', '@' + str(baseline['validated_at'])]
    cursor = previous.get('cursor') if previous.get('baseline') == baseline['validated_at'] else None
    result = subprocess.run(args + (['--after-cursor', cursor] if cursor else []), text=True, capture_output=True, timeout=10)
    if result.returncode and cursor:
        result = subprocess.run(args, text=True, capture_output=True, timeout=10)
    if result.returncode:
        raise RuntimeError('无法读取内核存储日志，暂停业务容器自动恢复')
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    if rows:
        atomic(path, {'baseline': baseline['validated_at'], 'cursor': rows[-1]['__CURSOR']})
    return [str(row.get('MESSAGE', '')) for row in rows]


def storage_fault(messages, device):
    name = Path(device).name
    for message in messages:
        if re.search(r'(?<![\w])' + re.escape(name) + r'(?![\w])', message) and re.search(r'I/O error|potential data loss|read-only|aborted journal|EXT4-fs error|failed to convert', message, re.I):
            return message[:1500]
    return None


def check_storage(c):
    from nasctl import mounted, verify_mount, volume
    host = Path(c['host'])
    if (host / 'state/deployment-blocked').exists():
        raise RuntimeError('存储保护已开启，暂停业务容器自动恢复')
    if not mounted(c):
        raise RuntimeError('数据库未受控挂载，不自动创建或挂载替代目录')
    device = verify_mount(c)
    options = subprocess.check_output(['findmnt', '-n', '-o', 'OPTIONS', '--target', c['mount']], text=True).strip().split(',')
    if 'ro' in options or 'emergency_ro' in options:
        return '数据库文件系统已变为只读'
    baseline = read(host / 'state/storage-validation.json', {})
    if baseline.get('image_uuid') != c['image_uuid'] or baseline.get('image') != str(Path(c['image']).resolve()) or not baseline.get('validated_at'):
        raise RuntimeError('缺少当前数据库镜像的只读检查记录，暂停业务容器自动恢复')
    if baseline['validated_at'] > time.time() + 5:raise RuntimeError('系统时钟早于存储检查记录，暂停自动恢复')
    fault = storage_fault(journal(c, baseline), device)
    if fault:
        return fault
    volume(c)
    if shutil.disk_usage(c['mount']).free < 1024 ** 3:
        raise RuntimeError('数据库剩余空间不足，暂停自动恢复')
    return None


def definition(c):
    global _DEFINITION
    from nasctl import DOCKER
    from nas_access import validate_compose
    paths = [Path(c['host']) / 'config/nas.yml', Path(c['host']) / 'config/compose.env']
    key = tuple((str(p), p.stat().st_mtime_ns) for p in paths)
    if _DEFINITION is None or _DEFINITION[0] != key:
        value = json.loads(subprocess.check_output(DOCKER + ['compose', '--project-name', c['project'], '--env-file', str(paths[1]), '-f', str(paths[0]), '--profile', '*', 'config', '--format', 'json'], text=True, timeout=20))
        validate_compose(c, value)
        _DEFINITION = (key, value)
    return _DEFINITION[1]


def quarantine(c, reason):
    from nasctl import stop, audit, inventory, DOCKER, BUSINESS
    marker = Path(c['host']) / 'state/deployment-blocked'
    marker.write_text('Automatic storage protection: ' + reason + '\n')
    audit(c, 'recovery-storage-block', reason)
    for module in BUSINESS + ['identity']:
        try:
            stop(c, module, remember_selection=False)
        except Exception:
            audit(c, 'recovery-stop-error', {'module': module})
    database = next((r for r in inventory(c) if r['service'] == 'database'), None)
    if database and database['state'] == 'running':
        # A failed graceful shutdown leaves the image mounted for inspection; never force-kill/unmount.
        result = subprocess.run(DOCKER + ['exec', '--user', 'postgres', database['name'], 'pg_ctl', '-D', '/var/lib/postgresql/data', 'stop', '-m', 'fast', '-t', '60'], capture_output=True, text=True, timeout=70)
        audit(c, 'recovery-database-stop', {'clean': result.returncode == 0})


def dependencies_ready(service, spec, rows):
    dependencies = dict(spec.get('depends_on') or {})
    if service == 'immich-machine-learning':
        dependencies['immich-server'] = {'condition': 'service_healthy'}
    for name, rule in dependencies.items():
        row = rows.get(name, {})
        if row.get('state') != 'running':
            return False
        if rule.get('condition') == 'service_healthy' and row.get('health') != 'healthy':
            return False
    return True


def tick(c, now=None):
    from nasctl import lock, desired_state, MODULES, inventory, compose, audit, volume
    now = time.time() if now is None else now
    if not enabled(c):
        return {'state': 'paused'}
    with lock():
        statepath = Path(c['host']) / 'state/container-recovery.json'
        state = read(statepath, {'services': {}})
        before = json.dumps(state, sort_keys=True)
        try:
            fault = check_storage(c)
            if fault:
                quarantine(c, fault)
                state['blocked'] = fault
                atomic(statepath, state)
                return {'state': 'storage_blocked'}
            state.pop('blocked', None)
            storage_ready = True
        except RuntimeError as error:
            state['blocked'] = str(error)
            storage_ready = False
        rows = {r['service']: r for r in inventory(c)}
        selected = desired_state(c)['modules']
        expected = set(s for module in selected if module != 'ml' or c.get('ml_enabled') for s in MODULES[module])
        state['services'] = {name: record for name, record in state['services'].items() if name in expected}
        specs = definition(c)['services']
        priority = ['meta', 'tailscale', 'database', 'cache', 'authentik-server', 'authentik-worker', 'identity-gateway']
        ordered = priority + sorted(expected - set(priority))
        for service in ordered:
            if service not in expected:
                continue
            row = rows.get(service, {})
            record = state['services'].setdefault(service, {})
            if row.get('state') == 'running':
                if row.get('health') == 'healthy':
                    record.update(state='healthy',error=None)
                    record.setdefault('healthy_since', now)
                    if now - record['healthy_since'] >= STABLE:
                        record.clear(); record.update(state='healthy', healthy_since=now)
                continue
            if row.get('state') not in (None, 'exited', 'dead', 'created'):
                continue
            record.pop('healthy_since', None)
            if not storage_ready and service not in SAFE_SERVICES:
                record['state'] = 'storage_blocked'
                continue
            if service == 'meta' and not storage_ready:
                try:volume(c)
                except Exception:
                    record['state']='storage_blocked';continue
            if service == 'immich-machine-learning':
                total = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:'))) * 1024
                if total < 5 * 1024 ** 3:
                    record['state'] = 'memory_blocked'; continue
            if service not in specs or not dependencies_ready(service, specs[service], rows):
                record['state'] = 'waiting_dependencies'; continue
            attempts = [stamp for stamp in record.get('attempts', []) if stamp > now - WINDOW]
            record['attempts'] = attempts
            if len(attempts) >= len(BACKOFF):
                record.update(state='cooldown', next_retry=attempts[0] + WINDOW)
                continue
            if now < record.get('next_retry', 0):
                record['state'] = 'retry_wait'; continue
            attempts.append(now)
            record.update(state='recovering', last_attempt=now, next_retry=now + BACKOFF[len(attempts) - 1])
            atomic(statepath, state)
            audit(c, 'container-auto-recover', {'service': service, 'attempt': len(attempts)})
            attempted_at=time.monotonic()
            try:
                compose(c, 'up', '-d', '--no-deps', '--no-build', '--wait', '--wait-timeout', '120', service)
                rows = {r['service']: r for r in inventory(c)}
                if rows.get(service, {}).get('state') != 'running' or rows[service].get('health') != 'healthy':
                    raise RuntimeError('not healthy')
                record.update(state='healthy', healthy_since=now+int(time.monotonic()-attempted_at), error=None)
                audit(c, 'container-auto-recovered', {'service': service})
            except Exception:
                record.update(state='retry_wait', next_retry=now+int(time.monotonic()-attempted_at)+BACKOFF[len(attempts)-1], error='自动恢复未就绪，请检查容器日志')
                audit(c, 'container-auto-recover-failed', {'service': service})
            # Recheck storage before recovering another container on the next pass.
            break
        if before != json.dumps(state, sort_keys=True):
            atomic(statepath, state)
        return state


def event_command(c, since=None):
    from nasctl import DOCKER
    # Replay from connection setup to close the subscribe/snapshot race. On reconnect
    # a full reconciliation also covers events no longer retained by the daemon.
    return DOCKER + ['events', '--format', '{{json .}}', '--since', str(int(time.time()) - 1 if since is None else since),
                     '--filter', 'type=container', '--filter', 'label=com.docker.compose.project=' + c['project']]


def relevant_event(c, event):
    attrs = event.get('Actor', {}).get('Attributes', {})
    return (event.get('Type') == 'container' and attrs.get('com.docker.compose.project') == c['project']
            and attrs.get('com.docker.compose.oneoff', 'False').lower() != 'true'
            and event.get('Action', '').split(':', 1)[0] in {'die', 'destroy', 'start', 'stop', 'unpause', 'health_status'})


def notify(events):
    try: events.put_nowait(True)
    except queue.Full: pass  # One pending reconciliation covers the whole project.


def watch_events(c, events, stopped):
    while not stopped.is_set():
        process = None
        try:
            process = subprocess.Popen(event_command(c), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            notify(events)  # Initial/reconnection inventory, not a periodic scan.
            for line in process.stdout:
                if stopped.is_set(): break
                try: event = json.loads(line)
                except ValueError: continue
                if relevant_event(c, event): notify(events)
        except OSError:
            pass
        finally:
            if process is not None:
                if process.poll() is None: process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
                process.stdout.close()
        stopped.wait(5)  # Only reconnect a lost event stream; never poll healthy containers.


def retry_delay(state, now=None):
    now = time.time() if now is None else now
    deadlines = [record['next_retry'] for record in state.get('services', {}).values()
                 if record.get('state') in {'retry_wait', 'cooldown'} and record.get('next_retry') is not None]
    return max(1, min(deadlines) - now) if deadlines else None


def reconcile(c):
    from nasctl import audit
    try:
        return retry_delay(tick(c))
    except Exception as error:
        message = str(error)
        # Do not lose exit events received during a backup or another lifecycle action.
        if '互斥维护' not in message:
            try: audit(c, 'container-recovery-error', message)
            except Exception: pass
        return 5


def serve():
    from nasctl import cfg
    c = cfg()
    events = queue.Queue(maxsize=1)
    stopped = threading.Event()
    threading.Thread(target=watch_events, args=(c, events, stopped), daemon=True).start()
    delay = None
    try:
        while True:
            try: events.get(timeout=delay)
            except queue.Empty: pass  # A pending recovery's backoff elapsed.
            delay = reconcile(c)
    finally:
        stopped.set()
