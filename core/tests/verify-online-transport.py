#!/usr/bin/env python3
"""Opt-in isolated real Restic runs: live RO mount, local encrypted repo, no cloud."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import argparse,hashlib,json,os,secrets,shutil,tempfile
from unittest.mock import patch
import nas_cloud as cloud,nas_online as online,nas_database
from nasctl import cfg

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--run',action='store_true');args=parser.parse_args()
 if not args.run:raise SystemExit('Read source then use --run; only random fixture files and containers are used')
 c=cfg();base=Path(c['host'])/'runtime';work=Path(tempfile.mkdtemp(prefix='online-transport-',dir=base))
 for name in ('state','config','secrets','runtime','data','repository','logs'):(work/name).mkdir()
 shutil.copyfile(Path(c['host'])/'config/versions.json',work/'config/versions.json')
 (work/'secrets/backup-password').write_text(secrets.token_urlsafe(32));(work/'secrets/cloud-rclone.conf').write_text('')
 fixture={**c,'host':str(work),'data':str(work/'data'),'backup_root':str(work/'unused-backups')}
 account={'id':'fixture-online','provider':'aliyun','name':'Local fixture only','state':'ready'}
 cloud.save(work/'state/cloud-accounts.json',{'accounts':[account]})
 repo='rclone:fixture-online:repository';actual_run=cloud.backup_run;checks=[]
 def local_run(arguments,**kwargs):
  arguments=list(arguments)
  network=arguments.index('--network');arguments[network+1]='none'
  arguments=[('/repo' if value==repo else value) for value in arguments]
  position=arguments.index('--entrypoint')
  arguments[position:position]=['--mount','type=bind,source='+str(work/'repository')+',target=/repo']
  return actual_run(arguments,**kwargs)
 def metadata(label):
  stage=work/'runtime'/('online-backup-'+label);stage.mkdir();(stage/'data').mkdir()
  databases={}
  for database in nas_database.application_databases(fixture):
   filename='database.dump' if database==nas_database.primary_database() else database+'.dump'
   (stage/filename).write_text('fake export for transport fixture');databases[database]={'dump':filename}
  for name in ('configuration.tar','database-roles.sql'):(stage/name).write_text('fixture')
  files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in stage.iterdir() if p.is_file()}
  manifest={'schema':5,'layout':'online-v1','consistency':'eventual','time':stage.name,'data_root':str((work/'data').resolve()),'databases':databases,'files':files}
  (stage/'manifest.json').write_text(json.dumps(manifest));(stage/'METADATA_READY').write_text(stage.name+'\n');online.validate(fixture,stage);return stage
 def checksum(snapshot,path):
  output=cloud.command(fixture,['sh','-c','set -eu; restic -r "$1" dump "$2" "$3" >/tmp/sample; sha256sum /tmp/sample','sh',repo,snapshot,path])
  return output.split()[0]
 with patch.object(cloud,'backup_run',side_effect=local_run),patch('nasctl.volume'):
  cloud.command(fixture,['restic','-r',repo,'init'])
  original=os.urandom(2*1024*1024);(work/'data/original.bin').write_bytes(original);(work/'data/deleted.txt').write_text('old-version')
  first=metadata('first');one=cloud.backup(fixture,first,online=True)['accounts'][0]['snapshot_id']
  assert checksum(one,'/backup-set/data/original.bin')==hashlib.sha256(original).hexdigest();checks.append('live source encrypted and recovered with matching SHA-256')
  assert list((first/'data').iterdir())==[];checks.append('host metadata directory contains no copied user files')
  (work/'data/deleted.txt').unlink();(work/'data/new.txt').write_text('new-version')
  second=metadata('second');two=cloud.backup(fixture,second,online=True)['accounts'][0]['snapshot_id']
  assert checksum(one,'/backup-set/data/deleted.txt')==hashlib.sha256(b'old-version').hexdigest()
  assert checksum(two,'/backup-set/data/new.txt')==hashlib.sha256(b'new-version').hexdigest();checks.append('old version survives source deletion and a later backup')
  listing=cloud.command(fixture,['restic','-r',repo,'ls','--json',two]);paths=[json.loads(line).get('path') for line in listing.splitlines() if line.startswith('{')]
  assert '/backup-set/data/deleted.txt' not in paths;checks.append('latest view omits deleted source file')
  remote=json.loads(cloud.command(fixture,['restic','-r',repo,'dump',two,'/backup-set/manifest.json']))
  assert remote['consistency']=='eventual' and remote['schema']==5;checks.append('cloud metadata explicitly labels eventual consistency')
  try:cloud.command(fixture,['sh','-c','touch /backup-set/data/should-not-write'],source=second,live_data=fixture['data'])
  except RuntimeError:pass
  else:raise RuntimeError('Worker wrote to live data')
  assert not (work/'data/should-not-write').exists();checks.append('real worker cannot write to source')
  cloud.command(fixture,['restic','-r',repo,'check','--read-data']);checks.append('encrypted repository data check passed')
 report={'state':'PASS','checks':checks,'scope':'isolated local backend; no production PostgreSQL or Aliyun account validation'}
 (work/'result.json').write_text(json.dumps(report,indent=2));print('PASS:',len(checks),'online transport checks;',work/'result.json')

if __name__=='__main__':main()
