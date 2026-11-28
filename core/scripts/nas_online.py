"""Online metadata exports plus a read-only live data mount: no full local copy."""
import contextlib,datetime,json,re,shutil,tempfile
from pathlib import Path
from nas_backup_control import ACTIVE,check,run,save

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()

def prepare(c,stage):
    from nasctl import DOCKER,inventory,volume,verify_mount
    from nas_database import application_databases,primary_database
    from nas_backup_config import archive_config
    from nas_continuous import inventory as data_inventory
    volume(c);verify_mount(c);check()
    rows={row['service']:row for row in inventory(c)}
    if rows.get('database',{}).get('state')!='running' or rows['database'].get('health') not in (None,'healthy'):
        raise RuntimeError('共享数据库未就绪，在线备份暂缓；不会自动启动已关闭的服务')
    # Fail closed on outside-root links/special files; scan only, never copy data.
    data_inventory(c['data']);check()
    container=c['project']+'-database-1'
    result=run(DOCKER+['exec',container,'psql','-U','postgres','-d','postgres','-Atc','SELECT COALESCE(SUM(pg_database_size(oid)),0) FROM pg_database WHERE NOT datistemplate'],capture_output=True,text=True,check=True)
    estimated=int(result.stdout.strip())*2+64*1024**2
    critical=c.get('critical_image_outer_free',c.get('critical_outer_free',0))
    if shutil.disk_usage(stage).free<critical+estimated:raise RuntimeError('在线导出空间不足，保留既有备份')
    manifest={'schema':5,'layout':'online-v1','consistency':'eventual','time':stage.name,'started':now(),'data_root':str(Path(c['data']).resolve()),'databases':{},'files':{}}
    state=Path(c['host'])/'state/cloud-online.json'
    save(state,{'state':'running','phase':'exporting','source':stage.name,'consistency':'eventual'})
    if ACTIVE.get():ACTIVE.get().update(phase='exporting',consistency='eventual')
    for database in application_databases(c):
        check()
        if not re.fullmatch(r'[A-Za-z0-9_]+',database):raise RuntimeError('Invalid registered database')
        filename='database.dump' if database==primary_database() else database+'.dump'
        started=now()
        with (stage/filename).open('wb') as output:
            run(DOCKER+['exec',container,'pg_dump','-U','postgres','-d',database,'-Fc'],stdout=output,check=True)
        if not (stage/filename).stat().st_size:raise RuntimeError('数据库导出为空，拒绝标记成功')
        manifest['databases'][database]={'dump':filename,'started':started,'finished':now()}
    with (stage/'database-roles.sql').open('wb') as output:
        run(DOCKER+['exec',container,'pg_dumpall','-U','postgres','--roles-only','--no-role-passwords'],stdout=output,check=True)
    archive_config(c,stage/'configuration.tar');check()
    from nas_mirror import digest_file
    for path in stage.iterdir():manifest['files'][path.name]=digest_file(path)
    manifest['prepared']=now()
    (stage/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    (stage/'METADATA_READY').write_text(stage.name+'\n')
    (stage/'data').mkdir()  # Empty host mountpoint; live data is mounted only inside the worker.
    return manifest

def validate(c,stage):
    from nas_mirror import unsafe,digest_file
    stage=Path(stage)
    if unsafe(stage) or stage.parent.resolve()!=(Path(c['host'])/'runtime').resolve() or not stage.name.startswith('online-backup-'):
        raise RuntimeError('Invalid online metadata directory')
    manifest=json.loads((stage/'manifest.json').read_text())
    if manifest.get('schema')!=5 or manifest.get('consistency')!='eventual' or manifest.get('time')!=stage.name:
        raise RuntimeError('Invalid online metadata manifest')
    if (stage/'METADATA_READY').read_text().strip()!=stage.name:raise RuntimeError('Incomplete online metadata')
    if manifest.get('data_root')!=str(Path(c['data']).resolve()) or unsafe(Path(c['data'])):raise RuntimeError('Live source identity changed')
    if unsafe(stage/'data') or not (stage/'data').is_dir() or any((stage/'data').iterdir()):raise RuntimeError('Online mountpoint must remain empty on the host')
    from nas_database import application_databases
    if set(manifest.get('databases',{}))!=set(application_databases(c)):raise RuntimeError('Online database exports incomplete')
    required={'configuration.tar','database-roles.sql'}|{item['dump'] for item in manifest['databases'].values()}
    if not required<=set(manifest.get('files',{})):raise RuntimeError('Online recovery metadata missing')
    for name,digest in manifest['files'].items():
        if Path(name).name!=name or unsafe(stage/name) or digest_file(stage/name)!=digest:raise RuntimeError('Online metadata checksum mismatch')
    return manifest

@contextlib.contextmanager
def bundle(c):
    root=Path(c['host'])/'runtime'
    from nas_mirror import unsafe
    if unsafe(root):raise RuntimeError('Linked runtime directory refused')
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='online-backup-',dir=root) as directory:
        stage=Path(directory);state=Path(c['host'])/'state/cloud-online.json'
        details={'source':stage.name,'consistency':'eventual','data_bytes_copied':0}
        try:
            save(state,{**details,'state':'running','phase':'preparing'})
            prepare(c,stage);validate(c,stage)
            details['metadata_bytes']=sum(path.stat().st_size for path in stage.iterdir() if path.is_file())
            save(state,{**details,'state':'running','phase':'uploading'})
            yield stage
            save(state,{**details,'state':'success','phase':'complete','finished':now()})
        except BaseException as error:
            from nas_backup_control import Interrupted
            save(state,{**details,'state':'interrupted' if isinstance(error,Interrupted) else 'failed','error':str(error),'finished':now()})
            raise
        # Only this job's small metadata files are removed by TemporaryDirectory.
