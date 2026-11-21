import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
from pathlib import Path as _NasPath
_nas_sys.path.insert(0,str(_NasPath(__file__).resolve().parents[1]/'scripts'))
import base64,hashlib,json,urllib.request,urllib.error,uuid,datetime
from pathlib import Path
h=Path('/mnt/c/nas/system');op=urllib.request.build_opener(urllib.request.ProxyHandler({}));base='http://127.0.0.1:2283/api';password=(h/'secrets/admin-password.txt').read_text().strip()
def request(path,data=None,token=None):
 headers={'Content-Type':'application/json'}
 if token:headers['Authorization']='Bearer '+token
 req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
 with op.open(req,timeout=60) as r:return json.load(r)
config=request('/server/config')
if not config['isInitialized']:
 request('/auth/admin-sign-up',{'email':'admin@nas.local','name':'NAS Admin','password':password})
 (h/'secrets/immich-account.txt').write_text('Email: admin@nas.local\nPassword is stored in admin-password.txt\n')
login=request('/auth/login',{'email':'admin@nas.local','password':password});token=login['accessToken']
image=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aEpcAAAAASUVORK5CYII=')
file=Path('/mnt/d/nas/system/runtime/acceptance/test.png');file.write_bytes(image)
boundary='nas-'+uuid.uuid4().hex;parts=[]
fields={'deviceAssetId':'nas-acceptance-'+uuid.uuid4().hex,'deviceId':'nas-acceptance','fileCreatedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(),'fileModifiedAt':datetime.datetime.now(datetime.timezone.utc).isoformat()}
for k,v in fields.items():parts.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+k+'"\r\n\r\n'+v+'\r\n').encode())
parts.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="assetData"; filename="nas-acceptance.png"\r\nContent-Type: image/png\r\n\r\n').encode()+image+b'\r\n');parts.append(('--'+boundary+'--\r\n').encode())
req=urllib.request.Request(base+'/assets',data=b''.join(parts),headers={'Authorization':'Bearer '+token,'Content-Type':'multipart/form-data; boundary='+boundary})
with op.open(req,timeout=60) as r:asset=json.load(r)
req=urllib.request.Request(base+'/assets/'+asset['id']+'/original',headers={'Authorization':'Bearer '+token})
with op.open(req,timeout=60) as r:download=r.read()
assert hashlib.sha256(image).digest()==hashlib.sha256(download).digest()
result={'state':'PASS','version':request('/server/version'),'asset_id':asset['id'],'sha256':hashlib.sha256(image).hexdigest(),'checks':['admin initialization','authenticated login','PNG upload','original download checksum'],'limitations':['No physical-phone test','Machine learning awaiting memory decision','Video and face/search acceptance pending']}
Path('/mnt/d/nas/system/runtime/acceptance/immich.json').write_text(json.dumps(result,indent=2))
print('PASS: Immich initialized; test image uploaded and downloaded with matching checksum. Credentials not printed.')
