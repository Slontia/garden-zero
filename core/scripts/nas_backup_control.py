"""Shared backup scheduling and cooperative interruption, independent of app IDs."""
import contextlib, contextvars, datetime, fcntl, json, os, re, shutil, subprocess, tempfile, threading, time, uuid
from pathlib import Path

ACTIVE = contextvars.ContextVar('backup_control', default=None)
DEFAULTS = {'time': '03:00', 'max_pause_minutes': 15}
_HOST_ZONE={}; _HOST_ZONE_LOCK=threading.Lock()

def host_timezone():
    """WSL schedules follow Windows; native Linux uses its OS timezone rules."""
    if 'microsoft' not in os.uname().release.lower():return None
    with _HOST_ZONE_LOCK:
        if time.monotonic()-_HOST_ZONE.get('checked',-float('inf'))<60:return _HOST_ZONE['zone']
        ps=shutil.which('powershell.exe') or '/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe'
        code="$ProgressPreference='SilentlyContinue'; $z=[TimeZoneInfo]::Local; [pscustomobject]@{name=$z.Id;seconds=[int]$z.GetUtcOffset([DateTimeOffset]::UtcNow).TotalSeconds}|ConvertTo-Json -Compress"
        try:
            result=subprocess.run([ps,'-NoProfile','-NonInteractive','-Command',code],capture_output=True,text=True,timeout=8,check=True)
            value=json.loads(result.stdout)
            zone=datetime.timezone(datetime.timedelta(seconds=int(value['seconds'])),value['name'])
        except (OSError,ValueError,KeyError,subprocess.SubprocessError):
            raise RuntimeError('无法读取物理宿主机时区，暂缓自动备份') from None
        _HOST_ZONE.update(checked=time.monotonic(),zone=zone);return zone

class Interrupted(InterruptedError): pass

def read(path, default):
    try: return json.loads(path.read_text())
    except FileNotFoundError: return default

def save(path, value):
    if path.is_symlink(): raise RuntimeError('Linked backup control file refused')
    fd, name = tempfile.mkstemp(prefix='.backup-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, ensure_ascii=False); f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
    finally: Path(name).unlink(missing_ok=True)

def settings(c):
    stored=read(Path(c['host'])/'state/backup-settings.json', {})
    return {key:stored.get(key,value) for key,value in DEFAULTS.items()}

def configure(c, data):
    value = {k:data.get(k) for k in DEFAULTS}
    if not isinstance(value['time'], str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value['time']):
        raise RuntimeError('请选择有效的每日开始时间')
    if type(value['max_pause_minutes']) is not int or not 1 <= value['max_pause_minutes'] <= 120:
        raise RuntimeError('最长停写时长应为 1–120 分钟')
    save(Path(c['host'])/'state/backup-settings.json', value)
    return value

def daily_slot(c, now=None):
    value=settings(c); now=now or datetime.datetime.now(datetime.timezone.utc)
    local=now.astimezone(host_timezone())
    if local.strftime('%H:%M') < value['time']: return None
    return local.strftime('%Y-%m-%d')

def alive(record):
    try: return Path('/proc/'+str(record['pid'])+'/stat').read_text().split()[21] == record['process_start']
    except (OSError, KeyError): return False

def status(c):
    row=read(Path(c['host'])/'state/backup-operation.json', {})
    if row.get('state') in ('running','cancelling') and not alive(row): row={**row,'state':'interrupted'}
    return {'settings':settings(c), 'operation':row}

def begin(c, kind):
    path=Path(c['host'])/'state/backup-operation.json'
    with (path.parent/'backup-control.lock').open('a') as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        old=read(path,{})
        if old.get('state') in ('running','cancelling') and alive(old): raise RuntimeError('已有备份任务正在进行')
        row={'id':uuid.uuid4().hex,'kind':kind,'state':'running','started':datetime.datetime.now(datetime.timezone.utc).isoformat(),'pid':os.getpid(),'process_start':Path('/proc/self/stat').read_text().split()[21]}
        save(path,row)
    return row

def cancel(c, ident):
    path=Path(c['host'])/'state/backup-operation.json'
    with (path.parent/'backup-control.lock').open('a') as f:
        fcntl.flock(f,fcntl.LOCK_EX); row=read(path,{})
        if row.get('id')!=ident or row.get('state') not in ('running','cancelling') or not alive(row):
            raise RuntimeError('该备份任务已结束，请刷新页面')
        save(path.parent/('backup-cancel-'+ident+'.json'), {'requested':True})
        save(path,{**row,'state':'cancelling'})
        current=ACTIVE.get()
        if current and current.row['id']==ident:current.cancelled=True
    return {'state':'cancelling'}

class Control:
    def __init__(self,c,row):
        self.c=c; self.row=row; self.deadline=None; self.suspended=False
        self.next_poll=0; self.cancelled=False
    def update(self, **values):
        path=Path(self.c['host'])/'state/backup-operation.json'
        with (path.parent/'backup-control.lock').open('a') as f:
            fcntl.flock(f,fcntl.LOCK_EX); current=read(path,{})
            if current.get('id')==self.row['id']: save(path,{**current,**values})
    def check(self):
        if self.suspended:return
        now=time.monotonic()
        if self.deadline is not None and now>=self.deadline: raise Interrupted('达到最长停写时长，备份已中断')
        # Avoid an extra DrvFS metadata request for every directory entry/block.
        if now>=self.next_poll:
            self.cancelled=self.cancelled or (Path(self.c['host'])/'state'/('backup-cancel-'+self.row['id']+'.json')).exists()
            self.next_poll=now+.25
        if self.cancelled:raise Interrupted('用户已中断备份')
    def pause(self):
        self.check(); limit=settings(self.c)['max_pause_minutes']*60
        self.deadline=time.monotonic()+limit
        self.update(phase='consistent_capture',pause_limit_seconds=limit,pause_started=datetime.datetime.now(datetime.timezone.utc).isoformat())
    def resume(self):
        self.deadline=None; self.suspended=True; self.update(phase='restoring')
    def resumed(self):
        self.suspended=False; self.update(phase='transfer',resumed=datetime.datetime.now(datetime.timezone.utc).isoformat())

@contextlib.contextmanager
def operation(c, kind, row=None):
    if ACTIVE.get() is not None:
        yield ACTIVE.get();return
    control=Control(c,row or begin(c,kind)); token=ACTIVE.set(control)
    state='success'; error=None
    try: yield control
    except Interrupted as exc: state='interrupted';error=str(exc);raise
    except BaseException as exc: state='failed';error=str(exc);raise
    finally:
        try:
            control.update(state=state,error=error,finished=datetime.datetime.now(datetime.timezone.utc).isoformat())
            (Path(c['host'])/'state'/('backup-cancel-'+control.row['id']+'.json')).unlink(missing_ok=True)
        finally:ACTIVE.reset(token)

def check():
    if ACTIVE.get(): ACTIVE.get().check()

def run(args, **kwargs):
    """Cancellation-aware child wait; use normal waits for lifecycle stop/start."""
    control=ACTIVE.get()
    if not control or control.suspended:return subprocess.run(args,**kwargs)
    control.check(); require=kwargs.pop('check',False); timeout=kwargs.pop('timeout',None); data=kwargs.pop('input',None)
    if kwargs.pop('capture_output',False): kwargs.update(stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if data is not None:kwargs['stdin']=subprocess.PIPE
    started=time.monotonic()
    with subprocess.Popen(args,**kwargs) as p:
        first=True
        try:
            while True:
                try:
                    out,err=p.communicate(data if first else None,timeout=.25);break
                except subprocess.TimeoutExpired:
                    first=False;control.check()
                    if timeout is not None and time.monotonic()-started>=timeout:raise subprocess.TimeoutExpired(args,timeout)
        except BaseException:
            p.terminate()
            try:p.communicate(timeout=5)
            except subprocess.TimeoutExpired:p.kill();p.communicate()
            raise
        result=subprocess.CompletedProcess(args,p.returncode,out,err)
        if require:result.check_returncode()
        control.check();return result
