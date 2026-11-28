"""Provider-independent, immutable incremental recovery points and change batching."""
import sys
sys.dont_write_bytecode = True
import concurrent.futures, datetime, hashlib, json, os, re, shutil, stat, tempfile, threading, time
from pathlib import Path
from nas_mirror import atomic, digest_file, unsafe, relative
from nas_backup_control import check

QUIET = 30
MIN_INTERVAL = 300
MAX_DELAY = 300
CHECKPOINT_INTERVAL = 3600


def read(path, default):
    return json.loads(path.read_text()) if path.is_file() else default


def signature(st):
    return [st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino, st.st_dev]


def cache_signature(st):
    return [st.st_size,st.st_mtime_ns,st.st_ino,stat.S_IMODE(st.st_mode)]


def inventory(root,*,workers=None):
    root = Path(root)
    if unsafe(root) or not root.is_dir(): raise RuntimeError('Data root missing or linked')
    rows = {}; folded=set()
    if workers is None:workers=8 if str(root).startswith('/mnt/') and 'microsoft' in os.uname().release.lower() else 1
    pool=concurrent.futures.ThreadPoolExecutor(max_workers=workers) if workers>1 else None
    def attributes(entries):
        for offset in range(0,len(entries),max(1,workers*4)):
            batch=entries[offset:offset+max(1,workers*4)]
            rows=list(pool.map(lambda entry:entry.stat(follow_symlinks=False),batch)) if pool else [entry.stat(follow_symlinks=False) for entry in batch]
            yield from zip(batch,rows)
    def walk(directory):
        with os.scandir(directory) as stream:
            entries = sorted(stream, key=lambda entry: entry.name)
        for entry,st in attributes(entries):
            check()
            path = Path(entry.path); name = relative(path.relative_to(root).as_posix())
            if name.casefold() in folded:raise RuntimeError('Case-colliding data paths cannot be restored safely')
            folded.add(name.casefold())
            if (stat.S_ISDIR(st.st_mode) and path.is_junction()) or not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
                raise RuntimeError('Unsupported link or special file in data; recovery point not committed')
            rows[name] = signature(st)
            if stat.S_ISDIR(st.st_mode): walk(path)
    try:walk(root)
    finally:
        if pool:pool.shutdown(wait=True)
    return rows


def roots(c):
    base = Path(c['backup_root'])
    if unsafe(base) or not base.is_dir() or base.resolve().is_relative_to(Path(c['data']).resolve()):
        raise RuntimeError('Unsafe incremental backup root')
    root = base / 'cloud'
    if unsafe(root): raise RuntimeError('Linked incremental backup root')
    root.mkdir(mode=0o700, exist_ok=True)
    return root


class Builder:
    """Caller verifies volume identity; changed files always replace a new inode.

    Reuse ignores only the mount-local device number, which can change across WSL
    restarts. The real volume UUID/GUID is checked by the caller before capture.
    """
    def __init__(self, c, stage,*,reserve=None):
        self.reserve=reserve if reserve is not None else max(1024**3,c.get('critical_image_outer_free',c.get('critical_outer_free',0)))+1024**3
        self.c = c; self.stage = Path(stage); self.data = self.stage / 'data'
        self.data.mkdir(); self.entries = {}; self.copied = 0; self.reused = 0;self.prepared=False
        self.previous = None; self.prior = {}
        last = read(Path(c['host']) / 'state/cloud-snapshot.json', {})
        if last.get('path'):
            previous = Path(last['path'])
            if previous.parent.resolve() != self.stage.parent.resolve() or unsafe(previous):
                raise RuntimeError('Invalid previous cloud generation')
            if (previous / 'COMPLETE').is_file():
                manifest = read(previous / 'manifest.json', {})
                if manifest.get('schema') != 4: raise RuntimeError('Unsupported cloud generation')
                self.previous = previous; self.prior = manifest['content']

    def prepare(self, final=False):
        rows = inventory(self.c['data'])
        for name, sig in rows.items():
            check()
            source = Path(self.c['data']) / name; target = self.data / name
            if stat.S_ISDIR(sig[0]):
                if target.exists() and not target.is_dir(): target.unlink()
                target.mkdir(parents=True, exist_ok=True)
                os.chmod(target,stat.S_IMODE(sig[0]));continue
            current = self.entries.get(name)
            if current and current['source_stat'] == sig:continue
            # Parents have already been visited by inventory's depth-first walk.
            if self.prepared and target.is_dir():shutil.rmtree(target)
            prior = self.prior.get(name)
            old = self.previous / 'data' / name if self.previous else None
            old_stat=None
            if prior and prior.get('source_stat',[])[:5]==sig[:5] and old:
                try:old_stat=old.lstat()
                except FileNotFoundError:pass
            if old_stat and stat.S_ISREG(old_stat.st_mode):
                if prior.get('snapshot_stat')!=cache_signature(old_stat) and digest_file(old)!=prior['sha256']:
                    raise RuntimeError('Previous cloud generation changed')
                target.unlink(missing_ok=True)
                if stat.S_IMODE(old_stat.st_mode)!=stat.S_IMODE(sig[0]):
                    if shutil.disk_usage(self.stage).free<sig[1]+self.reserve:raise RuntimeError('Insufficient local snapshot space')
                    shutil.copy2(old,target);os.chmod(target,stat.S_IMODE(sig[0]))
                else:
                    try:os.link(old,target)
                    except OSError:
                        if shutil.disk_usage(self.stage).free<sig[1]+self.reserve:raise RuntimeError('Insufficient local snapshot space')
                        shutil.copy2(old,target)
                self.entries[name]={**prior,'source_stat':sig,'snapshot_stat':cache_signature(target.stat()),'reused':True};self.reused+=1;continue
            if shutil.disk_usage(self.stage).free < sig[1] + self.reserve:
                raise RuntimeError('Insufficient local snapshot space; previous generation retained')
            temporary = None
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                with os.fdopen(fd, 'rb') as src, tempfile.NamedTemporaryFile(mode='wb',prefix='.garden-zero-copy-',dir=target.parent,delete=False) as dst:
                    temporary = Path(dst.name)
                    before = signature(os.fstat(src.fileno())); digest = hashlib.sha256()
                    remaining=sig[1]
                    while remaining:
                        check()
                        block=src.read(min(1024*1024,remaining))
                        if not block:break
                        dst.write(block);digest.update(block);remaining-=len(block)
                    dst.flush(); os.fsync(dst.fileno())
                    after = signature(os.fstat(src.fileno()))
                if remaining or before != sig or after != sig or signature(source.lstat()) != sig:
                    if final: raise RuntimeError('Data changed during consistent capture')
                    continue
                os.chmod(temporary,stat.S_IMODE(sig[0]))
                os.utime(temporary, ns=(source.stat().st_atime_ns, sig[2]))
                os.replace(temporary, target); self.copied += sig[1]
                self.entries[name] = {'size': sig[1], 'sha256': digest.hexdigest(), 'source_stat': sig,'snapshot_stat':cache_signature(target.stat()),'reused':False}
            finally:
                if temporary is not None:temporary.unlink(missing_ok=True)
        # Deletions affect only the new view; committed history stays untouched.
        for directory, dirs, files in os.walk(self.data, topdown=False):
            for name in files + dirs:
                path = Path(directory) / name; key = path.relative_to(self.data).as_posix()
                if key not in rows:
                    if path.is_dir(): path.rmdir()
                    else: path.unlink()
                    self.entries.pop(key, None)
        self.entries = {name: value for name, value in self.entries.items() if name in rows and stat.S_ISREG(rows[name][0])}
        if final:
            if inventory(self.c['data']) != rows: raise RuntimeError('Data changed during consistent capture')
            if set(self.entries) != {n for n,s in rows.items() if stat.S_ISREG(s[0])}: raise RuntimeError('Incomplete incremental content')
        self.final_rows=rows;self.prepared=True
        return rows


def validate(c, source,*,full=True):
    source = Path(source)
    if source.parent.resolve() != roots(c).resolve() or unsafe(source) or not (source/'COMPLETE').is_file():
        raise RuntimeError('Incomplete incremental recovery point')
    m = read(source/'manifest.json', {})
    if m.get('schema') != 4 or m.get('time') != source.name or not re.fullmatch(r'\d{8}-\d{6}', source.name):
        raise RuntimeError('Invalid incremental recovery point identity')
    if (source/'COMPLETE').read_text().strip() != source.name: raise RuntimeError('Invalid completion marker')
    required = {'database.dump', 'configuration.tar'} | {v['dump'] for v in m.get('databases',{}).values()}
    if not m.get('databases') or not required <= set(m.get('files',{})): raise RuntimeError('Missing database or recovery configuration')
    for name, checksum in m['files'].items():
        if Path(name).name != name or unsafe(source/name) or digest_file(source/name) != checksum: raise RuntimeError('Recovery payload checksum mismatch')
    tree = inventory(source/'data')
    actual = {name for name,sig in tree.items() if stat.S_ISREG(sig[0])}
    if actual != set(m.get('content',{})): raise RuntimeError('Recovery content manifest mismatch')
    for name, item in m['content'].items():
        relative(name)
        row=tree[name];cached=[row[1],row[2],row[4],stat.S_IMODE(row[0])]
        if row[1]!=item['size']:raise RuntimeError('Recovery content checksum mismatch')
        if (full or not item.get('reused',False) or item.get('snapshot_stat')!=cached) and digest_file(source/'data'/name)!=item['sha256']:
            raise RuntimeError('Recovery content checksum mismatch')
    return m


def trigger_tree(root,rows):
    from nas_apps import APPS
    ignored={path for app in APPS.values() for path in app.get('backup_ignore_changes',[])}
    result={}
    for name,sig in rows.items():
        if stat.S_ISDIR(sig[0]):
            # Child entries express additions/deletions. Directory touch times alone
            # are not user content changes and otherwise create a restart feedback loop.
            result[name]=[sig[0],sig[4]]
        elif name in ignored:
            # These six application health markers contain startup timestamps.
            # Capture them normally, but rewriting them must not trigger a new
            # writer pause and consequently another startup timestamp rewrite.
            result[name]=[sig[0]]
        else:result[name]=sig[:5]
    return result


def fingerprint(c,tree=None):
    """File/config changes batch quickly; database-only recovery points are hourly.

    Database heartbeat/session writes must not cause backup -> restart -> backup loops.
    Every file-triggered point still exports all databases with its attachments frozen.
    """
    rows=inventory(c['data']) if tree is None else tree
    config={d:inventory(Path(c['host'])/d) for d in ('config','secrets')}
    return hashlib.sha256(json.dumps([trigger_tree(c['data'],rows),config],sort_keys=True).encode()).hexdigest()


class Changes:
    def __init__(self): self.seen=None; self.first=None; self.changed=None; self.last_attempt=0
    def observe(self, value, now):
        if value != self.seen:
            self.seen=value; self.changed=now
            if self.first is None:self.first=now
    def due(self, now, checkpoint):
        if now-self.last_attempt < MIN_INTERVAL:return False
        return ((self.first is not None and (now-self.changed>=QUIET or now-self.first>=MAX_DELAY))
                or now-checkpoint>=CHECKPOINT_INTERVAL)
    def acknowledge(self, value):
        # Only acknowledge the state at capture time, never changes during cloud upload.
        if value == self.seen:self.first=self.changed=None


def pending_recovery_point(state,accounts):
    return bool(state.get('last_success') and state.get('path') and any(
        a.get('backup',{}).get('state')!='success' or a.get('backup',{}).get('source')!=state['last_success'] for a in accounts))


def daily_retry_due(daily, slot, state, accounts, job, now):
    return bool(slot and daily.get('day')==slot and job.get('state')!='interrupted'
        and state.get('state')=='success'
        and state.get('captured_at',0)>=daily.get('first_attempt_at',daily.get('attempted_at',now))
        and pending_recovery_point(state,accounts)
        and now-daily.get('attempted_at',0)>=MIN_INTERVAL)


def online_due(state,job,now):
    if job.get('state') in ('running','cancelling'):return False
    finished=0
    if job.get('finished'):
        finished=datetime.datetime.fromisoformat(job['finished'].replace('Z','+00:00')).timestamp()
    delay=1800 if job.get('state')=='interrupted' else MIN_INTERVAL
    return now>=max(state.get('next_at',0),finished+delay)


def serve():
    from nasctl import cfg, lock, audit, inventory
    from nas_cloud import automatic_accounts, start_backup, account_lock, maintain_accounts
    from nas_recovery import enabled
    from nas_backup_control import read as control_read, save as control_save, status as control_status
    while True:
        c=None
        try:
            c=cfg();now=time.time();path=Path(c['host'])/'state/cloud-online-auto.json'
            if enabled(c):maintain_accounts(c)
            if enabled(c) and automatic_accounts(c) and not (Path(c['host'])/'state/deployment-blocked').exists():
                state=control_read(path,{});job=control_status(c)['operation']
                if online_due(state,job,now):
                    # Probe at most once per interval, including while dependencies are disabled.
                    control_save(path,{'next_at':now+MIN_INTERVAL})
                    with lock(shared=True):
                        rows={row['service']:row for row in inventory(c)}
                        ready=all(rows.get(service,{}).get('state')=='running' and rows[service].get('health')=='healthy' for service in ('database','cloud-gateway'))
                    if ready:
                        with account_lock(c):start_backup(c,automatic=True)
        except Exception as error:
            if c and '互斥维护' not in str(error):
                try:audit(c,'continuous-backup-error',str(error))
                except Exception:pass
        time.sleep(30)


def confirmed(c, source):
    """Bound local working generations only after remote verification; never prune cloud history."""
    from nas_cloud import metadata
    root=roots(c);source=Path(source)
    if source.parent.resolve()!=root.resolve():return
    path=Path(c['host'])/'state/cloud-local-confirmed.json'
    known=read(path,{'sources':[]});known['sources']=sorted(set(known['sources'])|{source.name})
    atomic(path,known)
    pinned={a.get('backup',{}).get('source') for a in metadata(c)['accounts']}
    pinned.add(read(Path(c['host'])/'state/cloud-snapshot.json',{}).get('last_success'))
    generations=sorted(p for p in root.iterdir() if re.fullmatch(r'\d{8}-\d{6}',p.name) and p.is_dir() and not unsafe(p) and (p/'COMPLETE').is_file())
    pinned.update(p.name for p in generations[-2:])
    for generation in generations:
        if generation.name in known['sources'] and generation.name not in pinned:
            m=read(generation/'manifest.json',{})
            if m.get('schema')!=4 or m.get('time')!=generation.name:continue
            validate(c,generation,full=False)
            shutil.rmtree(generation)
    known['sources']=[name for name in known['sources'] if (root/name).is_dir()]
    atomic(path,known)
