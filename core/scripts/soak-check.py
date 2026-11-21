#!/usr/bin/env python3
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import json,time,urllib.request,uuid,hashlib
from pathlib import Path
from nasctl import cfg,inventory,verify_mount,output,DOCKER,read_state,volume,autostart
from nas_policy import observation

def probe(c):
    rows=inventory(c);active={x['service']:x for x in rows}
    disabled=[m for m in ('meta','files','knowledge','immich') if m not in autostart(c)]
    required={'meta':'meta','files':'files','joplin':'knowledge','immich-server':'immich'}
    if any(active.get(service,{}).get('state')!='running' and module in disabled for service,module in required.items()):
        return True
    volume(c);verify_mount(c)
    for row in rows:
        if row['oom']:raise RuntimeError('OOM recorded: '+row['service'])
    output(DOCKER+['exec','nas-database-1','psql','-v','ON_ERROR_STOP=1','-U','postgres','-d','immich','-Atc','SELECT 1'])
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for port,path in [(8080,'/health'),(8081,'/health'),(8082,'/api/ping'),(2283,'/api/server/ping')]:
        headers={}
        if port==8082:
            from urllib.parse import urlsplit
            base=dict(line.split('=',1) for line in (Path(c['host'])/'config/joplin.env').read_text().splitlines() if '=' in line)['APP_BASE_URL']
            headers['Host']=urlsplit(base).netloc
        with opener.open(urllib.request.Request('http://127.0.0.1:'+str(port)+path,headers=headers),timeout=10) as response:
            if response.status!=200:raise RuntimeError('HTTP probe failed: '+str(port))
    fixture=Path(c['root'])/'system/runtime/acceptance/immich.json'
    if fixture.is_file():
        expected=json.loads(fixture.read_text());asset=str(uuid.UUID(expected['asset_id']))
        sql="SELECT row_to_json(a)->>'originalPath' FROM asset a WHERE id='"+asset+"'"
        original=output(DOCKER+['exec','nas-database-1','psql','-v','ON_ERROR_STOP=1','-U','postgres','-d','immich','-Atc',sql])
        if original:
            relative=Path(original).relative_to('/data')
            path=Path(c['data'])/'immich'/relative
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected['sha256']:
                raise RuntimeError('Media probe checksum mismatch')
    return False

def main():
    c=cfg();p=Path(c['host'])/'state/soak.json';now=time.time();previous=read_state(c,'soak.json',{})
    problem=None;paused=False
    try:paused=probe(c)
    except Exception as error:problem=str(error)
    record=observation(previous,now,problem=problem,paused=paused,boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),storage_generation=c['image_uuid'])
    if record['history']!=previous.get('history',previous.get('errors',[])):
        history=Path(c['host'])/'logs/soak-history.jsonl'
        with history.open('a') as f:f.write(json.dumps({'time':now,'previous':previous,'current':record})+'\n')
    p.write_text(json.dumps(record,indent=2))
    print('NAS observation:',record['state'],'samples=',record['samples'],'continuous_seconds=',record['window_seconds'])
if __name__=='__main__':main()
