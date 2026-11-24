"""Private cloud account bridge. Provider authorization is separate from backup transport."""
import sys
sys.dont_write_bytecode = True
import base64, contextlib, datetime, fcntl, hashlib, http.cookiejar, json, os, re, secrets, subprocess, tempfile, time, threading
import urllib.error, urllib.parse, urllib.request
from pathlib import Path

DOCKER = ['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock']
BROKER = 'https://api.oplist.org.cn'
BRIDGE = 'http://127.0.0.1:5244'
PROVIDERS = {'aliyun': {'name': '阿里云盘', 'driver': 'AliyundriveOpen'}}

def save(path, value):
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError('Refusing linked cloud configuration')
    fd, temporary = tempfile.mkstemp(prefix='.cloud-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if Path(temporary).exists(): Path(temporary).unlink()

def read(path, default):
    return json.loads(path.read_text()) if path.is_file() else default

def metadata(c):
    return read(Path(c['host']) / 'state/cloud-accounts.json', {'schema': 1, 'accounts': []})

def process_alive(job):
    job=job or {}
    try:
        return Path('/proc',str(job['pid']),'stat').read_text().split()[21]==job['process_start']
    except (OSError,KeyError,IndexError):return False

def verification_alive(account):return process_alive(account.get('verification'))

def summary(c):
    rows=[]
    for account in metadata(c)['accounts']:
        row={k:account.get(k) for k in ('id','provider','name','remote_path','state','verified_at','error')}
        row['auto_backup']=account.get('auto_backup',True)
        row['backup']=dict(account.get('backup') or {})
        if not row['backup']:
            previous=read(Path(c['host'])/'state/cloud-backup.json',{})
            if any(a.get('account')==account['id'] for a in previous.get('accounts',[])):
                row['backup']={k:previous.get(k) for k in ('state','last_success','source','error')}
        if row['backup'].get('state')=='running' and not process_alive(account.get('backup_job')):row['backup']['state']='interrupted'
        row['profile']={k:v for k,v in (account.get('profile') or {}).items() if k in ('name','nick_name','user_name','user_id')}
        if account['state'] in ('verifying','initializing'):
            row['state']='verifying' if verification_alive(account) else 'failed'
            if row['state']=='failed':row['error']='连接验证已中断，请重新验证'
        rows.append(row)
    return rows

def profile(c,account):
    if account.get('provider')!='aliyun':return {}
    try:
        storage=api(c,'/api/admin/storage/get?id='+str(account['storage_id']))
        access=json.loads(storage['addition']).get('AccessToken')
        if not access:return {}
        result=request('https://openapi.alipan.com/adrive/v1.0/user/getDriveInfo',{},'Bearer '+access)
        return {k:str(result[k])[:200] for k in ('name','nick_name','user_name','user_id') if result.get(k)}
    except (RuntimeError,KeyError,ValueError):return {}

def start_verification(c,ident):
    value=metadata(c);account=next((a for a in value['accounts'] if a['id']==ident),None)
    if not account:raise RuntimeError('未知网盘账号')
    if account['state'] in ('verifying','initializing') and verification_alive(account):
        return {'state':'verifying','account':ident}
    job={'id':secrets.token_hex(12),'pid':os.getpid(),'process_start':Path('/proc/self/stat').read_text().split()[21]}
    account.update(state='verifying',error=None,verification=job)
    save(Path(c['host'])/'state/cloud-accounts.json',value)
    def work():
        try:
            from nasctl import LOCK_ROOT
            # The initiating HTTP request releases its lifecycle lock before this worker proceeds.
            with (LOCK_ROOT/'lifecycle.lock').open('a') as lifecycle:
                fcntl.flock(lifecycle,fcntl.LOCK_EX)
                with account_lock(c):verify(c,ident)
        except BaseException:
            with account_lock(c):
                current=metadata(c);item=next((a for a in current['accounts'] if a['id']==ident),None)
                if item and item.get('verification',{}).get('id')==job['id'] and item['state'] in ('verifying','initializing'):
                    item.update(state='failed',error='连接验证未完成，请重新验证')
                    save(Path(c['host'])/'state/cloud-accounts.json',current)
    thread=threading.Thread(target=work,name='cloud-verification-'+ident,daemon=True)
    try:thread.start()
    except Exception:
        account.update(state='failed',error='无法启动连接验证，请重试');save(Path(c['host'])/'state/cloud-accounts.json',value)
        raise RuntimeError(account['error']) from None
    return {'state':'verifying','account':ident}

def automatic_accounts(c):
    return [a for a in summary(c) if a['auto_backup'] and a['state']=='ready']

def automatic_enabled(c):
    return bool(automatic_accounts(c)) or bool(c.get('cloud_repository') and read(Path(c['host'])/'state/schedules.json',{'cloud':True}).get('cloud',True))

def retry_due(c, snapshot, previous):
    accounts=automatic_accounts(c);source=snapshot.get('last_success')
    if not accounts or snapshot.get('state')!='success' or not source or not re.fullmatch(r'\d{8}-\d{6}',source):return False
    marker=Path(c['backup_root'])/source/'COMPLETE'
    if not marker.is_file():return False
    for account in accounts:
        last=account['backup']
        if not last and previous.get('state')=='success' and previous.get('source')==source:continue
        if last.get('state')=='success' and last.get('source')==source:continue
        if account.get('verified_at') and marker.stat().st_mtime>=datetime.datetime.fromisoformat(account['verified_at']).timestamp():return True
    return False

@contextlib.contextmanager
def account_lock(c,blocking=False):
    with (Path(c['host']) / 'state/cloud-account.lock').open('a') as stream:
        try: fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError: raise RuntimeError('网盘账号操作正在进行，请稍后重试')
        yield

def event(c,operation,ident=None,**detail):
    from nas_apps import clean_log
    path=Path(c['host'])/'logs/cloud-events.jsonl';path.parent.mkdir(exist_ok=True)
    record={'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'operation':operation,'account':ident,**detail}
    line=clean_log(json.dumps(record,ensure_ascii=False))
    line=re.sub(r'Bearer\s+[A-Za-z0-9._~-]+','Bearer [redacted]',line,flags=re.I)
    line=re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+','[redacted-token]',line)
    fd=os.open(path,os.O_WRONLY|os.O_APPEND|os.O_CREAT,0o600)
    with os.fdopen(fd,'a') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX);stream.write(line+'\n')

def update_account(c,ident,**changes):
    with account_lock(c,blocking=True):
        value=metadata(c);account=next(a for a in value['accounts'] if a['id']==ident);account.update(changes)
        save(Path(c['host'])/'state/cloud-accounts.json',value)

def request(url, data=None, token=None, opener=None):
    headers = {'Content-Type': 'application/json'}
    if token: headers['Authorization'] = token
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers=headers)
    try:
        with (opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))).open(req, timeout=20) as response:
            return json.loads(response.read(2 * 1024 * 1024))
    except urllib.error.HTTPError as error:
        hint={401:'请重新授权',403:'请检查账号权限',429:'请求过于频繁，请稍后重试'}.get(error.code,'请稍后重试')
        raise RuntimeError('网盘接口返回 HTTP '+str(error.code)+'，'+hint) from None
    except Exception:
        # Exception URLs/bodies may contain authorization codes or tokens.
        raise RuntimeError('网盘接口请求失败，请检查直连网络或重新授权') from None

def api(c, path, data=None):
    token = (Path(c['host']) / 'secrets/cloud-bridge-token').read_text().strip()
    result = request(BRIDGE + path, data, token)
    if result.get('code') != 200:
        event(c,'gateway-api-failed',endpoint=path,code=result.get('code'),diagnostic=str(result.get('message',''))[:3000])
        raise RuntimeError('网盘连接组件拒绝操作，请检查账号授权和连接状态')
    return result.get('data')

def harden(c):
    """Remove default guest access and replace the short bootstrap administrator password."""
    marker = Path(c['host']) / 'secrets/cloud-bridge-hardened.json'
    if marker.is_file(): return
    users = api(c, '/api/admin/user/list?page=1&per_page=100')['content']
    for user in users:
        if user['role'] == 1:
            user['disabled'] = True
            api(c, '/api/admin/user/update', user)
        elif user['role'] == 2:
            user['password'] = secrets.token_urlsafe(48)
            api(c, '/api/admin/user/update', user)
    save(marker, {'hardened': True})

def broker(jar=None):
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(jar if jar is not None else http.cookiejar.CookieJar()))

def authorize_start(c, provider, name, account=None):
    if provider not in PROVIDERS: raise RuntimeError('暂时仅支持阿里云盘')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40: raise RuntimeError('请输入 1–40 字的账号名称')
    if not c.get('cloud_bridge_prepared'): raise RuntimeError('网盘连接组件尚未准备好')
    if account is not None and not any(a['id'] == account and a['provider'] == provider for a in metadata(c)['accounts']):
        raise RuntimeError('未知网盘账号，无法更新授权')
    jar = http.cookiejar.CookieJar()
    query = urllib.parse.urlencode({'driver': 'alicloud', 'driver_txt': 'alicloud_qr', 'server_use': 'true', 'server_set': 'true'})
    data = request(BROKER + '/alicloud/requests?' + query, opener=broker(jar))
    url, sid = data.get('text', ''), data.get('sid', '')
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('openapi.alipan.com', 'openapi.aliyundrive.com', 'www.aliyundrive.com', 'www.alipan.com') or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', sid):
        raise RuntimeError('授权服务返回了无效二维码')
    session = secrets.token_urlsafe(24)
    save(Path(c['host']) / 'secrets/cloud-authorization.json', {'session': session, 'provider': provider, 'name': name.strip(), 'account': account, 'sid': sid, 'expires': time.time() + 240,
        'cookies': [{'name': x.name, 'value': x.value, 'domain': x.domain, 'path': x.path, 'secure': x.secure} for x in jar]})
    return {'session': session, 'authorization_url': url, 'expires_in': 240}

def authorization(c, session):
    path = Path(c['host']) / 'secrets/cloud-authorization.json'
    data = read(path, {})
    if not isinstance(session, str) or not secrets.compare_digest(session, data.get('session', '')):
        raise RuntimeError('授权会话无效，请重新扫码')
    if data.get('expires', 0) < time.time():
        path.unlink(missing_ok=True)
        raise RuntimeError('二维码已过期，请重新扫码')
    return data

def authorize_poll(c, session):
    data = authorization(c, session)
    status = request('https://openapi.alipan.com/oauth/qrcode/' + data['sid'] + '/status').get('status')
    if status in ('WaitLogin', 'ScanSuccess'):
        return {'state': 'scanned' if status == 'ScanSuccess' else 'waiting'}
    if status != 'LoginSuccess': raise RuntimeError('授权已过期或取消，请重新扫码')
    jar = http.cookiejar.CookieJar()
    for item in data['cookies']:
        jar.set_cookie(http.cookiejar.Cookie(0, item['name'], item['value'], None, False, item['domain'], True, item['domain'].startswith('.'), item['path'], True, item['secure'], None, True, None, None, {}, False))
    token = request(BROKER + '/alicloud/callback?' + urllib.parse.urlencode({'code': data['sid'], 'grant_type': 'authorization_code'}), opener=broker(jar))
    if not token.get('refresh_token'): raise RuntimeError('未获得网盘授权，请重新扫码')
    account = attach(c, data, token['refresh_token'])
    (Path(c['host']) / 'secrets/cloud-authorization.json').unlink(missing_ok=True)
    start_verification(c,account['id'])
    return {'state': 'authorized', 'account': account,'verification_state':'verifying'}

def attach(c, auth, refresh_token):
    value = metadata(c)
    previous = next((x for x in value['accounts'] if x['id'] == auth.get('account')), None)
    if previous:
        storage = api(c, '/api/admin/storage/get?id=' + str(previous['storage_id']))
        addition = json.loads(storage['addition']); addition['refresh_token'] = refresh_token
        storage['addition'] = json.dumps(addition)
        api(c, '/api/admin/storage/update', storage)
        previous.update(name=auth['name'], state='authorized', error=None)
        save(Path(c['host']) / 'state/cloud-accounts.json', value)
        return {k: previous[k] for k in ('id', 'name', 'provider', 'state')}
    ident = 'aliyun-' + secrets.token_hex(4)
    mount = '/' + ident
    # Root is only mounted inside a private gateway with no NAS data mount.
    addition = {'root_folder_id': 'root', 'drive_type': 'default', 'refresh_token': refresh_token, 'use_online_api': True, 'alipan_type': 'default',
        'api_url_address': BROKER + '/alicloud/renewapi', 'remove_way': 'trash', 'callback_concurrency': 1}
    storage = api(c, '/api/admin/storage/create', {'mount_path': mount, 'driver': PROVIDERS[auth['provider']]['driver'], 'addition': json.dumps(addition), 'cache_expiration': 0, 'webdav_policy': 'native_proxy'})
    account = {'id': ident, 'provider': auth['provider'], 'name': auth['name'], 'mount': mount, 'storage_id': storage['id'], 'remote_path': 'GardenZero-Backup', 'state': 'authorized'}
    value['accounts'].append(account); save(Path(c['host']) / 'state/cloud-accounts.json', value)
    return {k: account[k] for k in ('id', 'name', 'provider', 'state')}

def command(c, args, source=None, input=None):
    versions = read(Path(c['host']) / 'config/versions.json', {})
    image = versions['CLOUD_BACKUP_IMAGE']['image']
    cmd = DOCKER + ['run', '--rm', '--label', 'nas.purpose=cloud-backup', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges:true',
        '--network', c['project'] + '_default', '--tmpfs', '/tmp:size=256m', '--memory', '768m', '-e', 'HOME=/tmp',
        '-e', 'HTTP_PROXY=', '-e', 'HTTPS_PROXY=', '-e', 'ALL_PROXY=', '-e', 'http_proxy=', '-e', 'https_proxy=', '-e', 'all_proxy=', '-e', 'NO_PROXY=*',
        '--mount', f"type=bind,source={c['host']}/secrets/cloud-rclone.conf,target=/credentials/rclone.conf,readonly",
        '--mount', f"type=bind,source={c['host']}/secrets/backup-password,target=/credentials/password,readonly", '-e', 'RESTIC_PASSWORD_FILE=/credentials/password']
    if source: cmd += ['--mount', f'type=bind,source={source},target=/backup-set,readonly']
    if input is not None: cmd += ['-i']
    cmd += ['--entrypoint', args[0], image, *args[1:]]
    result = subprocess.run(cmd, input=input, text=True, capture_output=True, timeout=7200 if source else 180)
    if result.returncode:
        event(c,'command-failed',command=args[:2],exit_code=result.returncode,diagnostic=result.stderr[-6000:])
        raise RuntimeError('网盘操作失败，诊断信息已记录；请检查授权、网络或远端完整性')
    return result.stdout

def remote_listing(c, path):
    result = api(c, '/api/fs/list', {'path': path, 'password': '', 'page': 1, 'per_page': 0, 'refresh': True})
    return result.get('content') or []

def verify(c, ident):
    value = metadata(c)
    account = next((x for x in value['accounts'] if x['id'] == ident), None)
    if not account: raise RuntimeError('未知网盘账号')
    root = account['mount']; folder = root + '/' + account['remote_path']
    statepath = Path(c['host']) / 'state/cloud-accounts.json'
    event(c,'verification-start',ident)
    try:
        items = remote_listing(c, root)
        account['profile']=profile(c,account) or account.get('profile',{})
        save(statepath,value)
        existing = next((x for x in items if x['name'] == account['remote_path']), None)
        if existing and not existing['is_dir']: raise RuntimeError('备份目录名称已被文件占用')
        if not existing: api(c, '/api/fs/mkdir', {'path': folder})
        contents = remote_listing(c, folder)
        # Unknown nonempty destinations must never be adopted or overwritten.
        if not account.get('repository_owned') and contents:
            raise RuntimeError('目标备份目录不是空目录，无法确认归属；请在网盘中保留原数据并使用空的 GardenZero-Backup 目录')
        privatepath = Path(c['host']) / 'secrets/cloud-webdav.json'
        private = read(privatepath, {})
        if ident not in private:
            password = secrets.token_urlsafe(36)
            api(c, '/api/admin/user/create', {'username': ident, 'password': password, 'base_path': folder, 'role': 0, 'permission': 1 + 256 + 512 + 8 + 16 + 32 + 64 + 128})
            private[ident] = password; save(privatepath, private)
        # rclone's obscure format is transport configuration, not encryption at rest.
        version = read(Path(c['host']) / 'config/versions.json', {})['RCLONE_IMAGE']['image']
        result = subprocess.run(DOCKER + ['run', '--rm', '-i', '--network', 'none', version, 'obscure', '-'], input=private[ident], text=True, capture_output=True, timeout=20, check=True)
        configpath = Path(c['host']) / 'secrets/cloud-rclone.conf'
        # Each account has its own constrained WebDAV user, common worker transport.
        import configparser
        config = configparser.ConfigParser(interpolation=None)
        if configpath.exists(): config.read(configpath)
        config[ident] = {'type': 'webdav', 'url': 'http://cloud-gateway:5244/dav/', 'vendor': 'other', 'user': ident, 'pass': result.stdout.strip()}
        fd, temporary = tempfile.mkstemp(prefix='.rclone-', dir=configpath.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w') as stream: config.write(stream); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, configpath)
        finally:
            if Path(temporary).exists(): Path(temporary).unlink()
        probe = 'garden-zero-probe-' + secrets.token_hex(12)
        payload = secrets.token_hex(4096)
        command(c, ['rclone', 'rcat', ident + ':' + probe], input=payload)
        downloaded = command(c, ['rclone', 'cat', ident + ':' + probe])
        if not secrets.compare_digest(hashlib.sha256(payload.encode()).digest(), hashlib.sha256(downloaded.encode()).digest()): raise RuntimeError('网盘上传下载校验不一致')
        command(c, ['rclone', 'deletefile', ident + ':' + probe])
        repo = 'rclone:' + ident + ':repository'
        if not account.get('repository_owned'):
            # Record ownership intent BEFORE init, so an interrupted init remains recoverable.
            account['repository_owned'] = True; account['state'] = 'initializing'; save(statepath, value)
            command(c, ['restic', '-r', repo, 'init'])
        elif not any(x['Path'] == 'config' for x in json.loads(command(c, ['rclone', 'lsjson', ident + ':repository', '--files-only']))):
            raise RuntimeError('仓库初始化被中断或远端配置缺失；保留现场，请由管理员检查后续传')
        command(c, ['restic', '-r', repo, 'check'])
        configuration = json.loads(command(c, ['restic', '-r', repo, 'cat', 'config']))
        if account.get('repository_id') and account['repository_id'] != configuration['id']:
            raise RuntimeError('远端仓库身份已改变，拒绝接管其他备份')
        account['repository_id'] = configuration['id']
        account.update(state='ready', verified_at=datetime.datetime.now(datetime.timezone.utc).isoformat(), error=None)
        save(statepath, value)
        event(c,'verification-success',ident)
        return {'state': 'ready', 'account': ident}
    except Exception as error:
        account.update(state='failed', error=str(error) if isinstance(error, RuntimeError) else '连接验证失败，请重试')
        save(statepath, value)
        event(c,'verification-failed',ident,error=account['error'])
        raise RuntimeError(account['error']) from None

def targets(c,identifiers=None,automatic=False):
    accounts=metadata(c)['accounts']
    if identifiers is not None:
        if not identifiers or any(not isinstance(i,str) or not any(a['id']==i for a in accounts) for i in identifiers):raise RuntimeError('未知网盘账号')
        accounts=[a for a in accounts if a['id'] in identifiers]
    if automatic:accounts=[a for a in accounts if a.get('auto_backup',True) and a['state']=='ready']
    if not accounts or any(a['state']!='ready' for a in accounts):raise RuntimeError('请先完成所选网盘账号授权与连接验证')
    return accounts

def backup(c,source,identifiers=None,automatic=False):
    source=Path(source)
    if source.is_symlink() or source.parent.resolve()!=Path(c['backup_root']).resolve() or not (source/'COMPLETE').is_file():raise RuntimeError('仅允许上传已完成的一致性快照')
    accounts=targets(c,identifiers,automatic);results=[];failures=[]
    for account in accounts:
        ident=account['id'];previous=next(a['backup'] for a in summary(c) if a['id']==account['id']);now=datetime.datetime.now(datetime.timezone.utc).isoformat()
        record={'state':'running','started':now,'source':source.name,'last_success':previous.get('last_success')}
        job={'pid':os.getpid(),'process_start':Path('/proc/self/stat').read_text().split()[21]}
        update_account(c,ident,backup=record,backup_job=job);event(c,'backup-start',ident,source=source.name)
        try:
            repo='rclone:'+ident+':repository'
            output=command(c,['restic','-r',repo,'backup','--json','--host','garden-zero','--tag','nas','/backup-set'],source=source)
            completed=next((json.loads(line) for line in reversed(output.splitlines()) if line.startswith('{') and json.loads(line).get('message_type')=='summary'),{})
            if not completed.get('snapshot_id'):raise RuntimeError('远端未确认完整快照，不能记录成功')
            command(c,['restic','-r',repo,'check'])
            now=datetime.datetime.now(datetime.timezone.utc).isoformat()
            record.update(state='success',finished=now,last_success=now,snapshot_id=completed['snapshot_id'])
            results.append({'account':ident,'snapshot_id':completed['snapshot_id']});event(c,'backup-success',ident,snapshot_id=completed['snapshot_id'])
        except Exception as error:
            record.update(state='failed',finished=datetime.datetime.now(datetime.timezone.utc).isoformat(),error=str(error));failures.append(ident)
            event(c,'backup-failed',ident,error=str(error))
        update_account(c,ident,backup=record)
    if failures:raise RuntimeError('部分网盘备份失败，请查看对应账号的结果')
    return {'mode':'encrypted_versioned_repository','accounts':results,'source':source.name}

def start_backup(c,ident=None):
    accounts=targets(c,[ident] if ident is not None else None)
    if any(a.get('backup',{}).get('state')=='running' and process_alive(a.get('backup_job')) for a in metadata(c)['accounts']):raise RuntimeError('已有网盘备份正在进行')
    now=datetime.datetime.now(datetime.timezone.utc).isoformat();job={'pid':os.getpid(),'process_start':Path('/proc/self/stat').read_text().split()[21]};ids=[a['id'] for a in accounts]
    value=metadata(c);known={a['id']:a['backup'] for a in summary(c)}
    for a in value['accounts']:
        if a['id'] in ids:a.update(backup={**known[a['id']],'state':'running','started':now,'error':None},backup_job=job)
    save(Path(c['host'])/'state/cloud-accounts.json',value)
    def work():
        statepath=Path(c['host'])/'state/cloud-backup.json';previous=read(statepath,{})
        record={'state':'running','started':now,'last_success':previous.get('last_success')};save(statepath,record)
        try:
            from nasctl import LOCK_ROOT,backup as snapshot
            from nas_mirror import Snapshot
            with (LOCK_ROOT/'lifecycle.lock').open('a') as lifecycle:
                fcntl.flock(lifecycle,fcntl.LOCK_EX)
                result=snapshot(c)
                if result['state']!='success':raise RuntimeError('未生成新的一致性快照，网盘备份未执行')
                with Snapshot(Path(result['path'])):result=backup(c,Path(result['path']),ids)
                record.update(result,state='success',last_success=datetime.datetime.now(datetime.timezone.utc).isoformat())
        except BaseException as error:
            message=str(error) if isinstance(error,RuntimeError) else '网盘备份未完成，请查看日志'
            record.update(state='failed',error=message)
            for ident in ids:
                account=next(a for a in metadata(c)['accounts'] if a['id']==ident)
                if account.get('backup',{}).get('state')=='running':update_account(c,ident,backup={**account['backup'],'state':'failed','error':message})
            event(c,'backup-job-failed',error=message)
        record['time']=datetime.datetime.now(datetime.timezone.utc).isoformat();save(statepath,record)
    try:threading.Thread(target=work,name='cloud-backup-job',daemon=True).start()
    except Exception:
        for a in value['accounts']:
            if a['id'] in ids:a['backup'].update(state='failed',error='无法启动备份任务，请重试')
        save(Path(c['host'])/'state/cloud-accounts.json',value)
        raise RuntimeError('无法启动备份任务，请重试') from None
    return {'state':'running','accounts':ids}

def remove_account(c,ident,confirmed):
    if confirmed is not True:raise RuntimeError('删除账号需要二次确认')
    value=metadata(c);account=next((a for a in value['accounts'] if a['id']==ident),None)
    if not account:raise RuntimeError('未知网盘账号')
    if (account['state'] in ('verifying','initializing') and verification_alive(account)) or (account.get('backup',{}).get('state')=='running' and process_alive(account.get('backup_job'))):raise RuntimeError('请等待当前验证或备份结束')
    archive_path=Path(c['host'])/'state/cloud-removed-accounts.json'
    archive=read(archive_path,{'accounts':[]})
    archive['accounts'].append({**{k:account.get(k) for k in ('id','provider','name','profile','remote_path','repository_owned','repository_id','backup','verified_at','auto_backup')},'removed_at':datetime.datetime.now(datetime.timezone.utc).isoformat()});save(archive_path,archive)
    users=api(c,'/api/admin/user/list?page=1&per_page=100')['content']
    for user in users:
        if user['username']==ident:api(c,'/api/admin/user/delete?id='+str(user['id']),{})
    api(c,'/api/admin/storage/delete?id='+str(account['storage_id']),{})
    privatepath=Path(c['host'])/'secrets/cloud-webdav.json';private=read(privatepath,{});private.pop(ident,None);save(privatepath,private)
    import configparser,io
    configpath=Path(c['host'])/'secrets/cloud-rclone.conf'
    if configpath.exists():
        config=configparser.ConfigParser(interpolation=None);config.read(configpath);config.remove_section(ident)
        stream=io.StringIO();config.write(stream);fd,temporary=tempfile.mkstemp(prefix='.cloud-config-',dir=configpath.parent)
        with os.fdopen(fd,'w') as output:output.write(stream.getvalue());output.flush();os.fsync(output.fileno())
        os.replace(temporary,configpath)
    value['accounts']=[a for a in value['accounts'] if a['id']!=ident];save(Path(c['host'])/'state/cloud-accounts.json',value)
    event(c,'account-removed',ident,remote_data_deleted=False)
    return {'state':'removed'}

def action(c, data):
    with account_lock(c):
        operation = data['action']
        if operation == 'cloud-authorize': return authorize_start(c, data.get('provider'), data.get('name'), data.get('account'))
        if operation == 'cloud-authorize-poll': return authorize_poll(c, data.get('session'))
        if operation == 'cloud-authorize-cancel':
            authorization(c, data.get('session'))
            (Path(c['host']) / 'secrets/cloud-authorization.json').unlink(missing_ok=True)
            return {'state': 'cancelled'}
        if operation == 'cloud-backup':return start_backup(c,data.get('account'))
        if operation == 'cloud-delete':return remove_account(c,data.get('account'),data.get('confirmed'))
        if operation == 'cloud-auto':
            value=metadata(c);account=next((a for a in value['accounts'] if a['id']==data.get('account')),None)
            if account is None or not isinstance(data.get('enabled'),bool):raise RuntimeError('无效的自动备份设置')
            account['auto_backup']=data['enabled'];save(Path(c['host'])/'state/cloud-accounts.json',value)
            return {'auto_backup':account['auto_backup']}
        if operation == 'cloud-verify': return start_verification(c, data.get('account'))
        raise RuntimeError('未知网盘操作')
