#!/usr/bin/env python3
"""Isolated lifecycle intent and directory ownership checks; never stop production."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import copy,json,tempfile
from pathlib import Path
from unittest.mock import patch
import nasctl as n
from nas_access import policy,resolve,validate_compose,access_mount,snapshot
checks=[]
def check(label,condition):
 if not condition:raise AssertionError(label)
 checks.append(label)
def rejects(label,fn):
 try:fn()
 except (RuntimeError,ValueError,TypeError):checks.append(label);return
 raise AssertionError(label)
with tempfile.TemporaryDirectory() as tmp:
 root=Path(tmp);(root/'state').mkdir();(root/'data').mkdir();c={'host':tmp,'data':str(root/'data'),'autostart':list(n.MODULES),'ml_enabled':True,'project':'fixture'}
 data=policy();check('nested/new paths inherit their unique owner',resolve(data,'immich/upload/new/image.jpg')['writer']=='immich-server')
 check('unknown directory has no default application writer',resolve(data,'new/path')['writer'] is None)
 check('legacy Markdown remains administrator-maintained',resolve(data,'joplin-legacy/articles/a.md')['writer']=='administrator' and 'joplin' in resolve(data,'joplin-legacy/articles/a.md')['readers'])
 check('synchronized clients use one NAS writer',resolve(data,'shared/new.txt')['writer']=='syncthing')
 check('versions and folder markers inherit shared ownership',resolve(data,'shared/.stversions/a~v.txt')['inherited_from']=='shared' and resolve(data,'shared/.stfolder')['writer']=='syncthing')
 config={'services':{'files':{'volumes':[{'type':'bind','source':c['data'],'read_only':True}]},'immich-server':{'volumes':[{'type':'bind','source':c['data']+'/immich'}]},'syncthing':{'volumes':[{'type':'bind','source':c['data']+'/shared'}]}}}
 check('actual allowed mount shapes validate',len(validate_compose(c,config))==3)
 rejects('read-only service cannot gain write access',lambda:access_mount(c,c['data']+'/shared','files',True))
 rejects('writer cannot cross into other app data',lambda:access_mount(c,c['data']+'/immich','syncthing',True))
 rejects('parent write cannot bypass children ownership',lambda:access_mount(c,c['data'],'syncthing',True))
 rejects('writable ancestor mount cannot bypass data ownership',lambda:access_mount(c,tmp,'files',True))
 check('all NAS apps may read all data without write access',all(access_mount(c,c['data'],service,False)['mode']=='read' for service in sum(n.MODULES.values(),[])))
 rejects('unregistered service cannot read app data',lambda:access_mount(c,c['data'],'unregistered',False))
 (root/'data/link').symlink_to('/tmp');rejects('linked data mount rejected',lambda:access_mount(c,c['data']+'/link','files',False))
 (root/'data/immich').mkdir();(root/'data/shared').mkdir();(root/'data/shared/alias').symlink_to(root/'data/immich')
 rejects('nested internal symlink cannot borrow another owner',lambda:access_mount(c,c['data']+'/shared/alias/upload','syncthing',True))
 bad=[{'Config':{'Labels':{'com.docker.compose.service':'files'}},'Mounts':[{'Type':'bind','Source':c['data'],'RW':True}]}]
 check('live mount drift is visible',snapshot(c,bad)['state']=='mismatch')
 n.remember(c,['knowledge'],False);check('stop selection persists on disk','knowledge' not in n.desired_state(c)['modules'])
 saved=(root/'state/desired-modules.json').read_bytes()
 with patch.object(n,'inventory',return_value=[]),patch.object(n,'unmount'),patch.object(n,'audit'),patch.object(n,'WRITER',type('Writer',(),{'revoke':lambda *a:None})()):n.stop(c,'all',remember_selection=False)
 check('clean host shutdown preserves intended state',(root/'state/desired-modules.json').read_bytes()==saved)
 with patch.object(n,'inventory',return_value=[]),patch.object(n,'audit'):n.stop(c,'immich')
 check('stopping Immich stops selection for both app and ML',all(m not in n.desired_state(c)['modules'] for m in ['immich','ml']))
 check('stopping app preserves ML feature preference',n.desired_state(c)['ml_preference'] is True)
 n.remember(c,['immich','ml'],True);n.remember(c,['ml'],False)
 check('ML preference can remain off while album remains on','immich' in n.autostart(c) and 'ml' not in n.autostart(c) and not n.desired_state(c)['ml_preference'])
 with patch.object(n,'output',return_value='{"services":{}}'),patch.object(n,'compose'),patch.object(n,'mount'),patch.object(n,'volume'),patch.object(n,'wait_services'),patch.object(n,'audit'),patch.object(n,'database_lock',__import__('contextlib').nullcontext),patch('nas_database.provision'):n.start(c,'immich')
 check('starting album honors remembered ML preference','ml' not in n.autostart(c))
 rejects('web cannot stop management connection',lambda:n.action(c,{'action':'stop','module':'tailscale'}))
 rejects('legacy autostart mutation is removed',lambda:n.action(c,{'action':'autostart','modules':list(n.MODULES)}))
 (root/'state/desired-modules.json').write_text('{broken')
 rejects('corrupt service selection fails closed',lambda:n.desired_state(c))
 check('systemd clean shutdown uses non-mutating intent command','nasctl.py shutdown' in (Path(__file__).parents[1]/'host/nas-session.service').read_text())
print('PASS:',len(checks),'application lifecycle/ownership checks; isolated fixtures only')
