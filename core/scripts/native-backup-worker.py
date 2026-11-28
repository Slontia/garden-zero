#!/usr/bin/env python3
"""Private host worker; no arbitrary commands or user-supplied filesystem paths."""
import sys
sys.dont_write_bytecode=True
import argparse,json,os,time,datetime
from pathlib import Path
from nasctl import cfg,lock,audit
from nas_backup_control import operation,check,save
from nas_native_jobs import handoff
from nas_native import Manager,enabled
from nas_cloud import metadata,update_account,process_alive


def main():
 parser=argparse.ArgumentParser();parser.add_argument('--job',required=True);args=parser.parse_args()
 from nas_stream import identifier
 ident=identifier(args.job);c=cfg()
 if not enabled(c):raise RuntimeError('Native backup engine is not enabled')
 # A plain CLI invocation cannot accidentally bypass the worker resource limits.
 group=Path('/proc/self/cgroup').read_text()
 if '/nas-native-'+ident+'.service' not in group:raise RuntimeError('Native uploads require their resource-limited service')
 specfile=Path(c['host'])/'state/native-jobs'/(ident+'.json')
 if specfile.is_symlink() or specfile.stat().st_size>16384:raise RuntimeError('Invalid native task specification')
 spec=json.loads(specfile.read_text());assert spec['id']==ident and 1<=len(spec['accounts'])<=64
 row=handoff(c,ident);job={'pid':row['pid'],'process_start':row['process_start']};failures=[]
 for account in metadata(c)['accounts']:
  if account['id'] in spec['accounts']:update_account(c,account['id'],backup_job=job)
 record={'state':'running','engine':'btrfs-v1','started':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 statuspath=Path(c['host'])/'state/cloud-backup.json';save(statuspath,record)
 try:
  with operation(c,'cloud',row):
   while True:
    check()
    try:lifecycle=lock(shared=True);lifecycle.__enter__();break
    except RuntimeError:time.sleep(.25)
   try:
    manager=Manager(c)
    for ident in spec['accounts']:
     check();account=next((a for a in metadata(c)['accounts'] if a['id']==ident),None)
     if account is None:continue
     try:manager.backup(account)
     except Exception as error:
      from nas_backup_control import Interrupted
      if isinstance(error,Interrupted):raise
      failures.append(ident);audit(c,'native-backup-failed',{'account':ident,'error':str(error)})
    if failures:raise RuntimeError('部分网盘备份未完成，既有恢复点保留；后台将重试')
   finally:lifecycle.__exit__(None,None,None)
  record.update(state='success',last_success=datetime.datetime.now(datetime.timezone.utc).isoformat())
 except BaseException as error:
  from nas_backup_control import Interrupted
  state='interrupted' if isinstance(error,Interrupted) else 'failed';record.update(state=state,error=str(error))
  for account in metadata(c)['accounts']:
   if account['id'] in spec['accounts'] and account.get('backup',{}).get('state')=='running':update_account(c,account['id'],backup={**account['backup'],'state':state,'error':str(error),'finished':datetime.datetime.now(datetime.timezone.utc).isoformat()})
  raise
 finally:
  record['finished']=datetime.datetime.now(datetime.timezone.utc).isoformat();save(statuspath,record);specfile.unlink(missing_ok=True)

if __name__=='__main__':
 try:main()
 except Exception as error:raise SystemExit('Native backup deferred: '+str(error))
