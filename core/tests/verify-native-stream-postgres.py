#!/usr/bin/env python3
"""Opt-in physical-base/WAL restore using isolated temporary PostgreSQL containers."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import argparse,json,secrets,subprocess,time,os,hashlib
import psycopg2
from psycopg2.extras import PhysicalReplicationConnection
from nas_stream import Repository,Journal,Missing
from nas_pg_stream import TappedBase,WalStream,WalWriter,position,lsn
from nas_btrfs_stream import ProcessStream

p=argparse.ArgumentParser();p.add_argument('--run',action='store_true');p.add_argument('--workspace',type=Path,required=True);p.add_argument('--report',type=Path,required=True);a=p.parse_args()
base=a.workspace.resolve();report=a.report.resolve();assert a.run and os.geteuid()==0 and base.parent==Path('/run/nas') and base.name.startswith('pg-stream-eval-') and base.is_dir() and not report.exists()
assert os.readlink('/proc/self/ns/mnt')!=os.readlink('/proc/1/ns/mnt')
D=['/usr/bin/docker','--host','unix:///var/run/docker.sock'];label='native-pg-recovery-fixture';suffix=secrets.token_hex(5);network='nas-pg-stream-test-'+suffix;source='nas-pg-stream-source-'+suffix;target='nas-pg-stream-target-'+suffix
image=json.loads(Path('/mnt/c/nas/system/config/versions.json').read_text())['POSTGRES_IMAGE']['image'];created=[];net=False;mounted=False;journal=None;outcome={'state':'running'}
def run(args,**kw):return subprocess.run(args,check=True,**kw)
def docker(args,**kw):return run(D+args,**kw)
def output(args):return subprocess.check_output(args,text=True).strip()
def ready(name):
 for _ in range(120):
  state=output(D+['inspect','--format','{{.State.Status}}',name])
  if state in ('exited','dead'):raise RuntimeError('Fixture PostgreSQL exited: '+name)
  main=subprocess.run(D+['exec',name,'cat','/proc/1/comm'],capture_output=True)
  if main.stdout.strip()==b'postgres' and subprocess.run(D+['exec',name,'pg_isready','-U','postgres'],capture_output=True).returncode==0:return
  time.sleep(.5)
 raise RuntimeError('Fixture PostgreSQL startup timeout: '+name)
def sql(name,query,database='postgres'):
 return output(D+['exec',name,'psql','-U','postgres','-d',database,'-Atc',query])
def start(name,restore=None):
 args=['run','-d','--name',name,'--label','nas.purpose='+label,'--network',network if restore is None else 'none','--read-only','--user','999:999','--cap-drop=ALL','--security-opt=no-new-privileges:true','--memory','256m','--memory-swap','256m','--cpus','0.25','--shm-size','32m','--tmpfs','/tmp:rw,size=16m','--tmpfs','/var/run/postgresql:rw,size=8m,uid=999,gid=999']
 if restore is None:args+=['--tmpfs','/var/lib/postgresql/data:rw,size=192m,uid=999,gid=999,mode=0700','-e','POSTGRES_HOST_AUTH_METHOD=trust','-e','POSTGRES_PASSWORD=fixture-only-password']
 else:args+=['--mount',f'type=bind,source={restore},target=/var/lib/postgresql/data']
 args+=['--entrypoint','/usr/local/bin/docker-entrypoint.sh',image,'postgres','-c','shared_buffers=16MB','-c','max_wal_size=64MB','-c','min_wal_size=32MB','-c','wal_level=replica','-c','max_replication_slots=0','-c','wal_keep_size=32MB']
 docker(args,stdout=subprocess.DEVNULL);created.append(name);ready(name)
class Store:
 def __init__(self):self.objects={}
 def empty(self):return not self.objects
 def get(self,name,limit):
  if name not in self.objects:raise Missing(name)
  value=self.objects[name];assert len(value)<=limit;return value
 def put(self,name,value):self.objects[name]=value
try:
 assert output(['findmnt','-n','-o','FSTYPE','--mountpoint',str(base)])=='tmpfs','Requires the caller-owned bounded tmpfs visible to Docker'
 docker(['network','create','--label','nas.purpose='+label,network],stdout=subprocess.DEVNULL);net=True;start(source)
 sql(source,"CREATE ROLE fixture_writer LOGIN NOSUPERUSER NOCREATEDB; CREATE ROLE fixture_backup LOGIN REPLICATION NOSUPERUSER PASSWORD 'fixture-replication-password'; CREATE TABLE baseline_only(id int);")
 for name in ['fixture_a','fixture_b']:
  sql(source,'CREATE DATABASE '+name+' OWNER fixture_writer');sql(source,"CREATE TABLE items(id integer PRIMARY KEY, body text); INSERT INTO items VALUES(1,'before');",name)
 ip=json.loads(output(D+['inspect',source]))[0]['NetworkSettings']['Networks'][network]['IPAddress']
 gateway=json.loads(output(D+['inspect',source]))[0]['NetworkSettings']['Networks'][network]['Gateway']
 hba=output(D+['exec',source,'cat','/var/lib/postgresql/data/pg_hba.conf'])
 rules='host replication fixture_backup '+gateway+'/32 scram-sha-256\nhost replication fixture_backup 127.0.0.1/32 scram-sha-256\n'
 docker(['exec','-i','--user','999:999',source,'tee','/var/lib/postgresql/data/pg_hba.conf'],input=(rules+hba+'\n').encode(),stdout=subprocess.DEVNULL);sql(source,'SELECT pg_reload_conf()')
 def connection():return psycopg2.connect(host=ip,user='fixture_backup',password='fixture-replication-password',dbname='replication',connect_timeout=5,connection_factory=PhysicalReplicationConnection)
 conn=connection()
 try:cur=conn.cursor();cur.execute('IDENTIFY_SYSTEM');system,timeline,*_=cur.fetchone()
 finally:conn.close()
 store=Store();repo=Repository(store,b'fixture-native-pg-encryption-key-material',initialize=True);journal=Journal(base/'journal.sqlite');base_id=secrets.token_hex(16)
 with ProcessStream(D+['exec','--user','999:999','-e','PGPASSWORD=fixture-replication-password',source,'pg_basebackup','-h','127.0.0.1','-U','fixture_backup','-D','-','-Ft','-X','fetch','--checkpoint=fast','--max-rate=8192','--manifest-checksums=SHA256']) as producer:
  tapped=TappedBase(producer);baseline=repo.upload('database',base_id,tapped,journal,producer={'kind':'postgres-base','system':system,'timeline':int(timeline)},finish=tapped.finish,chunk_size=1024**2)
 metadata=baseline['result'];assert metadata['timeline']==int(timeline);start_lsn=metadata['end_lsn'];print('Physical baseline committed',baseline['bytes'],'bytes',flush=True)
 sql(source,"INSERT INTO items VALUES(2,'after'); UPDATE items SET body='updated' WHERE id=1;",'fixture_a');sql(source,'DELETE FROM items WHERE id=1;','fixture_b');end=lsn(sql(source,'SELECT pg_current_wal_flush_lsn()'))
 wal_id=secrets.token_hex(16)
 with WalStream(connection,start_lsn,end,timeline=int(timeline),system_identifier=system) as wal:
  increment=repo.upload('database',wal_id,wal,journal,producer={'kind':'postgres-wal','system':system,'timeline':int(timeline),'start_lsn':start_lsn,'end_lsn':end},parent=base_id,finish=wal.finish,chunk_size=1024**2)
 print('WAL increment committed',increment['bytes'],'bytes',flush=True)
 restored=base/'restored';restored.mkdir(mode=0o700);os.chown(restored,999,999)
 tar=subprocess.Popen(['tar','-xpf','-','-C',str(restored)],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
 try:repo.restore(base_id,tar.stdin.write);tar.stdin.close();assert tar.wait(timeout=60)==0,tar.stderr.read().decode()
 finally:
  if tar.poll() is None:tar.terminate();tar.wait(timeout=10)
  tar.stderr.close()
 for directory,dirs,files in os.walk(restored):
  os.chown(directory,999,999)
  for name in files:os.chown(Path(directory)/name,999,999,follow_symlinks=False)
 docker(['run','--rm','--network','none','--read-only','--user','999:999','--memory','128m','--cpus','0.25','--mount',f'type=bind,source={restored},target=/restore,readonly','--entrypoint','pg_verifybackup',image,'/restore'],stdout=subprocess.DEVNULL)
 writer=WalWriter(restored/'pg_wal',start_lsn,end,int(timeline));repo.restore(wal_id,writer.write);writer.finish()
 auto=restored/'postgresql.auto.conf'
 with auto.open('a') as f:f.write("\nrestore_command='false'\nrecovery_target_timeline='"+str(timeline)+"'\nrecovery_target_action='promote'\n")
 (restored/'recovery.signal').touch();os.chown(restored/'recovery.signal',999,999);start(target,restored)
 assert lsn(sql(target,'SELECT pg_last_wal_replay_lsn()'))>=end
 assert sql(target,'SELECT id||\':\'||body FROM items ORDER BY id','fixture_a').splitlines()==['1:updated','2:after']
 assert sql(target,'SELECT COUNT(*) FROM items','fixture_b')=='0';assert sql(target,"SELECT rolname FROM pg_roles WHERE rolname='fixture_writer'")=='fixture_writer'
 assert sql(source,'SELECT COUNT(*) FROM pg_replication_slots')=='0'
 outcome={'state':'PASS','baseline_bytes':baseline['bytes'],'wal_bytes':increment['bytes'],'start_lsn':position(start_lsn),'end_lsn':position(end),'checks':['base archive and official pg_verifybackup validation','physical WAL replay restores inserts, updates and deletion across two databases','roles preserved','no replication slots or unbounded source retention'],'scope':'Isolated generated PostgreSQL fixtures only; no production database or cloud accessed'}
except BaseException as e:
 outcome={'state':'FAIL','error':str(e),'producer_diagnostic':bytes(producer.error).decode(errors='replace') if 'producer' in locals() else '','container_diagnostics':{name:subprocess.run(D+['logs','--tail','35',name],capture_output=True,text=True).stderr[-12000:] for name in created}};raise
finally:
 if journal:journal.close()
 cleanup=[]
 for name in reversed(created):
  try:
   assert output(D+['inspect','--format','{{index .Config.Labels "nas.purpose"}}',name])==label;docker(['rm','-f',name],stdout=subprocess.DEVNULL)
  except Exception as e:cleanup.append(str(e))
 if net:
  try:docker(['network','rm',network],stdout=subprocess.DEVNULL)
  except Exception as e:cleanup.append(str(e))
 if mounted:
  try:run(['umount',str(base)])
  except Exception as e:cleanup.append(str(e))
 if cleanup:outcome.update(state='CLEANUP_FAILED',cleanup_errors=cleanup)
 report.write_text(json.dumps(outcome,indent=2));print('PostgreSQL fixture report:',report,flush=True)
