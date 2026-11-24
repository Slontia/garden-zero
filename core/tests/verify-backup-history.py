#!/usr/bin/env python3
"""Disk backup identity, interrupted jobs and outcome retention in isolated fixtures."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import tempfile,json
from nas_backup_history import disk,begin,finish,history,preparation_failure
with tempfile.TemporaryDirectory() as directory:
 root=Path(directory);(root/'state').mkdir()
 c={'host':directory,'hdd_root':'/mnt/f/nas-backup','hdd_guid':'test-volume'}
 assert disk(c)['relative_path']=='nas-backup'
 assert disk({**c,'hdd_root':'/mnt/z/nas-backup'})==disk(c)
 (root/'state/hdd-volume.json').write_text(json.dumps({'uid':'test-volume','name':'Offline backup'}))
 assert disk(c)['name']=='Offline backup'
 assert disk({**c,'hdd_guid':'other-volume'})['name']!='Offline backup'
 previous={'state':'failed','started':'2026-11-23T00:00:00Z','last_success':'2026-11-22T00:00:00Z'}
 path=begin(c,{'state':'running','started':'2026-11-24T00:00:00Z'},previous)
 assert len(history(c,{}))==2 and history(c,{})[0]['state']=='running'
 record=json.loads(path.read_text());record['pid']=999999999;path.write_text(json.dumps(record))
 assert history(c,{})[0]['state']=='interrupted'
 finish(c,path,{'state':'success','started':'2026-11-24T00:00:00Z'})
 assert history(c,{})[0]['state']=='success'
 (root/'state/hdd-backup.json').write_text(json.dumps(previous))
 preparation_failure(c,RuntimeError('Fixture preparation failure'))
 assert len(history(c,{}))==3
 result=json.loads((root/'state/hdd-backup.json').read_text())
 assert result['state']=='failed' and result['last_success']==previous['last_success']
print('PASS: stable volume-relative identity, offline name, history migration, interruption and preparation failure')
