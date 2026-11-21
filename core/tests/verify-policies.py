#!/usr/bin/env python3
"""Boundary cases for missed backup windows and truthful continuous observation."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import datetime as dt,json
from pathlib import Path
from nas_policy import backup_due,backup_summary,observation
c={'backup_hour':3,'backup_keep':14};now=dt.datetime(2026,11,17,1,45)
cases=[]
def check(name,value):
    assert value,name;cases.append({'test':name,'state':'PASS'})
check('missed prior 03:00 catches up after midnight',backup_due(c,{'last_success':'20261116-005945'},now))
check('recent manual backup covers previous due time',not backup_due(c,{'last_success':'20261116-160000'},now))
check('before next due stays covered',not backup_due(c,{'last_success':'20261116-160000'},now.replace(hour=2)))
check('at next due backup is required',backup_due(c,{'last_success':'20261116-160000'},now.replace(hour=3,minute=0)))
check('failure does not hide stale last success',backup_due(c,{'state':'failed','last_success':'20261116-005945'},now))
check('no backup is due immediately',backup_due(c,{},now))
check('disabled plan not marked overdue',not backup_summary(c,{},now,False)['overdue'])
check('skipped backup retains old freshness',backup_summary(c,{'state':'skipped','last_success':'20261116-005945'},now)['overdue'])
start=1000000;state={}
for n in range(145):state=observation(state,start+n*600,boot_id='test')
check('24h continuous probe window passes',state['state']=='24h_probes_passed')
state=observation(state,start+86400+9000,boot_id='test')
check('gap resets continuous window',state['state']=='observing' and state['samples']==1 and state['window_seconds']==0)
check('gap history retained',state['history'][-1]['type']=='gap')
for n in range(1,145):state=observation(state,start+86400+9000+n*600,boot_id='test')
check('new window can pass without erasing old interruption',state['state']=='24h_probes_passed' and bool(state['history']))
failed=observation(state,state['last']+600,problem='checksum mismatch',boot_id='test')
check('failure cannot report pass',failed['state']=='fault' and failed['samples']==0)
fixed=observation(failed,failed['last']+600,boot_id='test')
check('recovery starts new window',fixed['state']=='observing' and fixed['samples']==1)
paused=observation(fixed,fixed['last']+600,paused=True,boot_id='test')
check('intentional stop is paused',paused['state']=='paused')
restarted=observation(fixed,fixed['last']+600,boot_id='another-boot')
check('new boot resets continuity',restarted['samples']==1 and restarted['window_seconds']==0)
legacy=observation({'state':'observing','started':100,'last':500,'samples':4,'errors':[{'error':'gap'}]},600,boot_id='test')
check('legacy failure history cannot count as continuous uptime',legacy['samples']==1 and legacy['window_seconds']==0 and len(legacy['history'])==2)
generation=observation({},start,boot_id='test',storage_generation='old-image')
generation=observation(generation,start+600,boot_id='test',storage_generation='old-image')
check('same image preserves observation continuity',generation['samples']==2)
generation=observation(generation,start+1200,boot_id='test',storage_generation='new-image')
check('new database image resets observation window',generation['samples']==1 and generation['window_seconds']==0 and generation['history'][-1]['type']=='storage_change')
root=Path(__file__).resolve().parents[2];(root/'system/runtime/acceptance/policies-20261117.json').write_text(json.dumps(cases,indent=2))
print('PASS:',len(cases),'backup/observation policy boundaries; no real data or clock changed.')
