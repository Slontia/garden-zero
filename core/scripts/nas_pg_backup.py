"""Shared-cluster physical-backup capability; no application-name dispatch."""
import ipaddress,json,os,re,secrets,subprocess,tempfile
from pathlib import Path
from nas_btrfs_stream import ProcessStream
from nas_pg_stream import TappedBase,WalStream
from nas_stream import IntegrityError,identifier


def admin(c,query):
 from nas_database import sql
 return sql(c,query,capture=True)

def service(c):return c['project']+'-database-1'

def details(c):
 from nasctl import DOCKER,verify_mount
 verify_mount(c)
 row=json.loads(subprocess.check_output(DOCKER+['inspect',service(c)],text=True))[0]
 if row['State']['Status']!='running' or row['State'].get('Health',{}).get('Status')!='healthy':raise RuntimeError('Shared database is not ready for native backup')
 network=row['NetworkSettings']['Networks'][c['project']+'_default']
 gateway=str(ipaddress.IPv4Address(network['Gateway']));host=str(ipaddress.IPv4Address(network['IPAddress']))
 query="SELECT json_build_object('system',system_identifier::text,'wal_level',current_setting('wal_level'),'synchronous',current_setting('synchronous_standby_names'),'segment_size',pg_size_bytes(current_setting('wal_segment_size')),'data',current_setting('data_directory'),'hba',current_setting('hba_file'),'version',current_setting('server_version_num'),'tablespaces',(SELECT COUNT(*) FROM pg_tablespace WHERE spcname NOT IN ('pg_default','pg_global'))) FROM pg_control_system();"
 values=json.loads(admin(c,query))
 if values['wal_level'] not in ('replica','logical') or values['synchronous'] or values['tablespaces']:raise RuntimeError('Database backup requires physical WAL, no synchronous standby dependency, and no external tablespaces')
 if values['hba']!=values['data'].rstrip('/')+'/pg_hba.conf':raise RuntimeError('Custom external database authentication configuration requires explicit backup integration')
 mounts=[m for m in row['Mounts'] if m['Destination']==values['data']]
 if len(mounts)!=1 or Path(mounts[0]['Source']).resolve()!=Path(c['mount'])/'postgres':raise IntegrityError('Database data mount does not match the managed image')
 return {**values,'host':host,'gateway':gateway,'image':row['Image'],'container':service(c)}


def prepare(c,*,apply=False):
 """One-time least-privilege role and scoped HBA rules; reload, never restart."""
 from nas_backup_control import save
 info=details(c);root=Path(c['host']);role=c['project']+'_backup'
 if not re.fullmatch('[a-z][a-z0-9_]{0,62}',role):raise IntegrityError('Invalid database backup role')
 record=root/'state/native-postgres.json';previous=json.loads(record.read_text()) if record.exists() else None
 exists=admin(c,"SELECT 1 FROM pg_roles WHERE rolname='"+role+"'")=='1'
 if exists and (not previous or previous.get('system')!=info['system'] or previous.get('role')!=role):raise IntegrityError('Existing database backup role has unknown ownership')
 if previous and previous.get('system')!=info['system']:raise IntegrityError('Database cluster identity changed; backup credentials must not be reused silently')
 if not apply:return {'state':'configured' if previous and previous.get('state')=='ready' else 'preparation_required','role':role,'system':info['system']}
 secret=root/'secrets/native-postgres-password'
 if secret.is_symlink():raise IntegrityError('Linked database backup secret refused')
 if not secret.exists():
  fd=os.open(secret,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
  with os.fdopen(fd,'w') as f:f.write(secrets.token_hex(32)+'\n')
 password=secret.read_text().strip()
 if not re.fullmatch('[a-f0-9]{64}',password):raise IntegrityError('Invalid private database backup credential')
 save(record,{'state':'preparing','role':role,'system':info['system']})
 admin(c,"BEGIN; SET LOCAL password_encryption='scram-sha-256'; DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='"+role+"') THEN CREATE ROLE "+role+" LOGIN REPLICATION NOSUPERUSER NOCREATEDB NOCREATEROLE; END IF; END $$; ALTER ROLE "+role+" WITH LOGIN REPLICATION NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '"+password+"'; GRANT CONNECT ON DATABASE postgres TO "+role+'; COMMIT;')
 hba=Path(c['mount'])/'postgres/pg_hba.conf'
 if hba.is_symlink():raise IntegrityError('Linked database authentication file refused')
 original=hba.read_text();begin='# GardenZero native backup BEGIN';end='# GardenZero native backup END'
 block=begin+'\n'+''.join('host '+db+' '+role+' '+address+'/32 scram-sha-256\n' for db in ('replication','postgres') for address in (info['gateway'],'127.0.0.1'))+end+'\n'
 if begin in original:
  left=original.index(begin);right=original.index(end,left)+len(end)
  if left!=0:raise IntegrityError('Backup authentication block moved; refusing to reorder user authentication rules')
  original=original[right:].lstrip('\n')
 saved=root/'state/pg_hba.before-native-backup'
 if not saved.exists():saved.write_text(hba.read_text())
 owner=hba.stat();fd,name=tempfile.mkstemp(prefix='.hba-native-',dir=hba.parent)
 try:
  os.fchmod(fd,owner.st_mode&0o777);os.fchown(fd,owner.st_uid,owner.st_gid)
  with os.fdopen(fd,'w') as f:f.write(block+original);f.flush();os.fsync(f.fileno())
  os.replace(name,hba)
 finally:Path(name).unlink(missing_ok=True)
 admin(c,'SELECT pg_reload_conf()')
 env=root/'secrets/native-postgres-client.env'
 if env.is_symlink():raise IntegrityError('Linked database backup environment refused')
 env.write_text('PGUSER='+role+'\nPGPASSWORD='+password+'\nPGHOST=127.0.0.1\nPGCONNECT_TIMEOUT=5\n');env.chmod(0o600)
 client=Client(c,allow_preparing=True);identity=client.identity()
 if identity['system']!=info['system']:raise IntegrityError('Database backup client reached another cluster')
 save(record,{'state':'ready','role':role,'system':info['system'],'image':info['image'],'gateway':info['gateway']});return {'state':'ready','role':role,'system':info['system']}

class Base:
 def __init__(self,client,attempt):
  from nasctl import DOCKER
  self.client=client;self.tag='garden-zero-'+identifier(attempt)
  args=DOCKER+['exec','--user','999:999','--env-file',str(Path(client.c['host'])/'secrets/native-postgres-client.env'),'-e','PGAPPNAME='+self.tag,client.info['container'],'nice','-n','19','pg_basebackup','-D','-','-Ft','-X','fetch','--checkpoint=spread','--max-rate=1024','--manifest-checksums=SHA256']
  self.producer=ProcessStream(args);self.tap=TappedBase(self.producer)
 def read(self,n):return self.tap.read(n)
 def finish(self):return self.tap.finish()
 def close(self):
  if self.producer.process.poll() is None:self.client.cancel(self.tag)
  self.producer.close()
 def __enter__(self):return self
 def __exit__(self,*error):self.close()

class Client:
 def __init__(self,c,*,allow_preparing=False):
  self.c=c;self.info=details(c);root=Path(c['host']);record=json.loads((root/'state/native-postgres.json').read_text())
  if record['system']!=self.info['system'] or record['state'] not in (('ready','preparing') if allow_preparing else ('ready',)):raise IntegrityError('Physical backup resource is not prepared for this cluster')
  self.role=record['role'];secret=root/'secrets/native-postgres-password'
  if secret.is_symlink():raise IntegrityError('Linked backup credential refused')
  self.password=secret.read_text().strip()
 def connect(self,replication=False):
  import psycopg2
  from psycopg2.extras import PhysicalReplicationConnection
  options={'host':self.info['host'],'user':self.role,'password':self.password,'connect_timeout':5,'application_name':'garden-zero-native-control'}
  if replication:options.update(dbname='replication',connection_factory=PhysicalReplicationConnection)
  else:options.update(dbname='postgres')
  return psycopg2.connect(**options)
 def identity(self):
  conn=self.connect(True)
  try:
   cursor=conn.cursor();cursor.execute('IDENTIFY_SYSTEM');system,timeline,flush,*_=cursor.fetchone()
   return {'system':str(system),'timeline':int(timeline),'flush_lsn':flush,'segment_size':self.info['segment_size'],'image':self.info['image']}
  finally:conn.close()
 def cancel(self,tag):
  try:
   conn=self.connect()
   try:
    conn.autocommit=True
    with conn.cursor() as cur:cur.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename=%s AND application_name=%s AND pid<>pg_backend_pid()',(self.role,tag))
   finally:conn.close()
  except Exception:pass  # Never kill a database process to stop its backup client.
 def base(self,attempt):return Base(self,attempt)
 def wal(self,start,end,identity,*,check=lambda:None):
  current=self.identity()
  if current['system']!=identity['system'] or current['timeline']!=identity['timeline']:raise IntegrityError('Database identity/timeline changed; new baseline required')
  return WalStream(lambda:self.connect(True),start,end,timeline=identity['timeline'],system_identifier=identity['system'],check=check)
