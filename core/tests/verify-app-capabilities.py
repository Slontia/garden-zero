#!/usr/bin/env python3
"""Application API semantics without starting production containers."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
from unittest.mock import patch
import nasctl as n
import nas_apps as a
checks=[]
def check(name,value):
 if not value:raise AssertionError(name)
 checks.append(name)
def rejects(name,fn):
 try:fn()
 except RuntimeError:checks.append(name);return
 raise AssertionError(name)
modules={m:{'state':'ready','containers':[{'service':s,'state':'running','health':'healthy'} for s in services]} for m,services in n.MODULES.items()}
apps=a.snapshot({},modules,{'ml_preference':True})
check('six applications cover all eleven services',len(apps)==6 and sum(len(x['services']) for x in apps.values())==11)
check('log tabs expose registered per-application sources',{x['id'] for x in apps['immich']['log_sources']}=={'immich-server','database','cache','immich-machine-learning'})
check('intelligence is a logical feature not a separate app',apps['immich']['features'][0]['id']=='intelligence' and 'ml' not in apps)
modules['immich']={'state':'stopped','containers':[]};modules['ml']={'state':'stopped','containers':[]}
app=a.snapshot({},modules,{'ml_preference':True})['immich'];check('stopped parent retains preference but disables feature',not app['features'][0]['available'] and app['features'][0]['enabled'] and not app['features'][0]['active'])
with patch.object(n,'inventory',return_value=[]),patch.object(n,'start') as start:
 rejects('feature enable rejects stopped parent before any start',lambda:a.feature({},'immich','intelligence',True));check('rejected feature cannot implicitly start album',not start.called)
with patch.object(n,'inventory',return_value=[]):rejects('legacy ML start cannot bypass parent requirement',lambda:n.start({'host':'/nonexistent'},'ml'))
with patch.object(n,'stop') as stop:
 a.power({},'immich',False);check('app power delegates grouped album lifecycle',stop.call_args.args==({},'immich'))
with patch.object(n,'start') as start:
 a.power({},'files',True);check('file app starts both logical access functions',[x.args[1] for x in start.call_args_list]==['files','smb'])
rejects('protected platform cannot be switched off',lambda:a.power({},'platform',False))
rejects('only boolean enabled values accepted',lambda:a.power({},'knowledge','true'))
rejects('arbitrary feature names cannot control containers',lambda:a.feature({},'immich','database',False))
with patch.object(n,'output') as output:
 rejects('logs reject unrelated Docker service',lambda:a.log_text({},'outside-project',True));check('invalid log service executes no command',not output.called)
check('terminal escape codes stripped without dropping text',a.clean_log('\x1b[31merror\x1b[0m\n\x1b]0;window-title\x07done')=='error\ndone')
print('PASS:',len(checks),'application abstraction/dependency checks')
