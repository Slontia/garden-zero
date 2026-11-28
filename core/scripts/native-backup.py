#!/usr/bin/env python3
"""Explicit native backup preparation and authenticated recovery-stream export.

No automatic format, application stop, cloud prune or production restore.
"""
import sys
sys.dont_write_bytecode=True
import argparse,json
from nasctl import cfg,lock
from nas_cloud import metadata
from nas_native import Manager,password
from nas_objects import RcloneStore
from nas_stream import Repository,identifier


def main():
 parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='action',required=True)
 prepare=commands.add_parser('prepare');prepare.add_argument('--apply',action='store_true')
 for name in ('inspect','export-stream'):
  command=commands.add_parser(name);command.add_argument('--account',required=True)
  if name=='export-stream':command.add_argument('--commit',required=True);command.add_argument('--stdout',action='store_true',required=True)
 args=parser.parse_args();c=cfg()
 if args.action=='prepare':
  from nas_pg_backup import prepare
  from nas_storage import verify
  verify(c)
  if not args.apply:print(json.dumps({'state':'review_only','database':prepare(c),'checkpoint_accounting':'not_changed','business_restart':False}));return
  # Initial accounting is maintenance, separate from daily commit work.
  with lock():result=Manager(c).prepare()
  print(json.dumps(result));return
 account=next((a for a in metadata(c)['accounts'] if a['id']==args.account),None)
 if not account or account.get('native_repository',{}).get('state')!='ready':raise RuntimeError('No verified native repository registered for that account')
 with lock(shared=True),RcloneStore(c,account) as store:
  repo=Repository(store,password(c),expected=account['native_repository']['id'])
  if args.action=='inspect':
   print(json.dumps({'repository':repo.id,'heads':{key:repo.head(key) for key in ('data','database','configuration')}},indent=2));return
  ident=identifier(args.commit)
  if sys.stdout.isatty():raise RuntimeError('Refusing to print a plaintext binary recovery stream on a terminal')
  repo.restore(ident,sys.stdout.buffer.write)
  sys.stdout.buffer.flush()

if __name__=='__main__':
 try:main()
 except Exception as error:raise SystemExit('Native backup: '+str(error))
