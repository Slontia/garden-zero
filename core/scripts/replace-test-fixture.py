"""Replace only our own incompatible tiny PNG fixture, retaining old asset in trash/backup."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import datetime,hashlib,json,shutil,struct,urllib.request,uuid,zlib
from pathlib import Path
root=Path('/mnt/d/nas');recordpath=root/'system/runtime/acceptance/immich.json';old=json.loads(recordpath.read_text());h=Path('/mnt/c/nas/system');op=urllib.request.build_opener(urllib.request.ProxyHandler({}));base='http://127.0.0.1:2283/api'
def req(path,data=None,method=None,token=None):
 headers={'Content-Type':'application/json'}
 if token:headers['Authorization']='Bearer '+token
 r=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,method=method,headers=headers)
 with op.open(r,timeout=60) as f:
  body=f.read();return json.loads(body) if body.strip() else None
token=req('/auth/login',{'email':'admin@nas.local','password':(h/'secrets/admin-password.txt').read_text().strip()})['accessToken']
asset=req('/assets/'+old['asset_id'],token=token)
if asset['originalFileName']!='nas-acceptance.png':raise RuntimeError('Not our acceptance fixture; refusing any asset modification')
def chunk(kind,body):return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body)&0xffffffff)
image=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',64,64,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b''.join(b'\x00'+bytes([40,120,200])*64 for _ in range(64))))+chunk(b'IEND',b'')
(root/'system/runtime/acceptance/test-valid.png').write_bytes(image)
boundary='nas-'+uuid.uuid4().hex;parts=[]
for k,v in {'deviceAssetId':'nas-valid-acceptance-'+uuid.uuid4().hex,'deviceId':'nas-acceptance','fileCreatedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'fileModifiedAt':datetime.datetime.now(datetime.timezone.utc).isoformat()}.items():parts.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+k+'"\r\n\r\n'+v+'\r\n').encode())
parts.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="assetData"; filename="nas-acceptance-valid.png"\r\nContent-Type: image/png\r\n\r\n').encode()+image+b'\r\n');parts.append(('--'+boundary+'--\r\n').encode())
r=urllib.request.Request(base+'/assets',data=b''.join(parts),headers={'Authorization':'Bearer '+token,'Content-Type':'multipart/form-data; boundary='+boundary})
with op.open(r,timeout=60) as f:new=json.load(f)
req('/assets',{'ids':[old['asset_id']],'force':False},'DELETE',token)
shutil.copy2(recordpath,recordpath.with_name('immich-initial-fixture.json'))
newrecord={'state':'uploaded-awaiting-ml','asset_id':new['id'],'sha256':hashlib.sha256(image).hexdigest(),'original_fixture_soft_trashed':old['asset_id'],'fixture_explanation':'Initial 1px fixture passed Pillow but libpng rejected its stream; replaced with standards-conformant generated PNG. Old original retained in trash and backup.'}
recordpath.write_text(json.dumps(newrecord,indent=2))
print('Valid synthetic PNG uploaded; only our prior test asset soft-trashed, not permanently deleted.')
