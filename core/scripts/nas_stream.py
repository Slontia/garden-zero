"""Bounded encrypted object streams with immutable commits and resumable journals.

Transport deliberately has no provider knowledge. No file-tree or history listing
is required after repository initialization. Restore validates every object before
returning plaintext; callers must receive into an isolated, unpublished target.
"""
import base64,datetime,hashlib,hmac,json,os,re,secrets,sqlite3
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CHUNK=16*1024**2
PAGE=1024
MAX_PAGES=4096
METADATA=2*1024**2
MAGIC=b'GNZ1'
ID=re.compile(r'[a-f0-9]{32}')
RESOURCE=re.compile(r'[a-z][a-z0-9_-]{0,63}')

class IntegrityError(RuntimeError):pass
class Missing(FileNotFoundError):pass

def encode(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def digest(data):return hashlib.sha256(data).hexdigest()
def identifier(value):
 if not isinstance(value,str) or not ID.fullmatch(value):raise IntegrityError('Invalid stream/repository identifier')
 return value

def path(value):
 if not isinstance(value,str) or not value or value.startswith('/') or any(p in ('','.','..') for p in value.split('/')):raise IntegrityError('Unsafe object path')
 return value

class Codec:
 def __init__(self,password,root):
  identifier(root['id'])
  if root.get('format')!='garden-zero-stream-v1':raise IntegrityError('Unsupported repository format')
  salt=base64.b64decode(root['salt'],validate=True)
  if len(salt)!=32 or len(password)<32:raise IntegrityError('Invalid repository key material')
  key=hashlib.scrypt(password,salt=salt,n=32768,r=8,p=1,dklen=32,maxmem=128*1024**2)
  self.key=hmac.digest(key,b'garden-zero:aead:v1','sha256');self.repo=root['id']
  proof=hmac.digest(key,b'garden-zero:ownership:v1'+encode({k:v for k,v in root.items() if k!='proof'}),'sha256').hex()
  if root.get('proof') and not hmac.compare_digest(root['proof'],proof):raise IntegrityError('Repository password/ownership mismatch')
  self.proof=proof
 def seal(self,name,data):
  nonce=secrets.token_bytes(12);return MAGIC+nonce+AESGCM(self.key).encrypt(nonce,data,encode([self.repo,path(name)]))
 def open(self,name,data,limit):
  if len(data)>limit+32 or len(data)<32 or data[:4]!=MAGIC:raise IntegrityError('Invalid encrypted object size/format')
  try:return AESGCM(self.key).decrypt(data[4:16],data[16:],encode([self.repo,path(name)]))
  except Exception:raise IntegrityError('Encrypted object authentication failed') from None

class Journal:
 """Only in-flight chunk metadata; fixed database ceiling, never payload caching."""
 def __init__(self,filename,limit=64*1024**2,namespace=''):
  if namespace:identifier(namespace)
  self.namespace=namespace
  file=Path(filename)
  if file.is_symlink() or (file.exists() and file.stat().st_size>limit):raise IntegrityError('Unsafe or oversized stream journal')
  self.db=sqlite3.connect(filename);self.db.execute('PRAGMA journal_mode=DELETE');self.db.execute('PRAGMA synchronous=FULL')
  self.db.execute('PRAGMA max_page_count='+str(limit//self.db.execute('PRAGMA page_size').fetchone()[0]))
  self.db.executescript('CREATE TABLE IF NOT EXISTS job(id TEXT PRIMARY KEY, identity BLOB NOT NULL, prepared BLOB); CREATE TABLE IF NOT EXISTS chunk(job TEXT, n INTEGER, size INTEGER, hash TEXT, PRIMARY KEY(job,n));')
 def begin(self,ident,identity):
  ident=self.key(ident)
  row=self.db.execute('SELECT identity FROM job WHERE id=?',(ident,)).fetchone();value=encode(identity)
  if row and row[0]!=value:raise IntegrityError('Stream producer or parent changed; refusing unsafe resume')
  with self.db:self.db.execute('INSERT OR IGNORE INTO job(id,identity) VALUES(?,?)',(ident,value))
 def key(self,ident):return self.namespace+':'+ident if self.namespace else ident
 def chunk(self,ident,n):return self.db.execute('SELECT size,hash FROM chunk WHERE job=? AND n=?',(self.key(ident),n)).fetchone()
 def acknowledged(self,ident,n,data):
  ident=self.key(ident)
  with self.db:self.db.execute('INSERT INTO chunk VALUES(?,?,?,?)',(ident,n,len(data),digest(data)))
 def pages(self,ident):
  ident=self.key(ident);offset=0
  while True:
   rows=self.db.execute('SELECT n,size,hash FROM chunk WHERE job=? AND n>=? ORDER BY n LIMIT ?',(ident,offset,PAGE)).fetchall()
   if not rows:return
   yield [{'n':n,'size':size,'sha256':sha} for n,size,sha in rows];offset+=len(rows)
 def prepared(self,ident,value=None):
  ident=self.key(ident)
  if value is not None:
   with self.db:self.db.execute('UPDATE job SET prepared=? WHERE id=?',(encode(value),ident))
  row=self.db.execute('SELECT prepared FROM job WHERE id=?',(ident,)).fetchone()
  return json.loads(row[0]) if row and row[0] else None
 def finish(self,ident):
  ident=self.key(ident)
  with self.db:self.db.execute('DELETE FROM chunk WHERE job=?',(ident,));self.db.execute('DELETE FROM job WHERE id=?',(ident,))
 def close(self):self.db.close()

class Repository:
 def __init__(self,store,password,expected=None,initialize=False):
  self.store=store
  try:root=json.loads(store.get('repository.json',METADATA))
  except Missing:
   if not initialize or not store.empty():raise IntegrityError('Unknown nonempty or missing repository; refusing initialization') from None
   root={'format':'garden-zero-stream-v1','id':expected or secrets.token_hex(16),'salt':base64.b64encode(secrets.token_bytes(32)).decode()}
   codec=Codec(password,root);root['proof']=codec.proof
   store.put('repository.json',encode(root))
   if store.get('repository.json',METADATA)!=encode(root):raise IntegrityError('Repository initialization round-trip failed')
  if expected and root['id']!=expected:raise IntegrityError('Remote repository identity changed')
  if not root.get('proof'):raise IntegrityError('Unauthenticated repository ownership')
  self.codec=Codec(password,root);self.id=root['id']
 def put(self,name,value):
  data=self.codec.seal(name,value);self.store.put(name,data)
  if self.codec.open(name,self.store.get(name,len(value)+32),len(value))!=value:raise IntegrityError('Remote upload/download verification failed')
 def get(self,name,limit=METADATA):return self.codec.open(name,self.store.get(name,limit+32),limit)
 def manifest(self,ident):
  value=json.loads(self.get('commits/'+identifier(ident)))
  if value.get('id')!=ident or value.get('repository')!=self.id:raise IntegrityError('Commit identity mismatch')
  return value
 def recover(self,resource,ident,journal,*,producer,parent=None,minimum=0,check=lambda:None):
  try:value=self.manifest(ident)
  except Missing:return None
  if value['resource']!=resource or value['producer']!=producer or value['parent']!=parent:raise IntegrityError('Owned pending commit does not match its durable intent')
  identity={'repository':self.id,'resource':resource,'producer':producer,'parent':parent,'previous':value['previous'],'chunk_size':value['chunk_size']}
  journal.begin(ident,identity);journal.prepared(ident,value)
  import io
  return self.upload(resource,ident,io.BytesIO(),journal,producer=producer,parent=parent,minimum=minimum,check=check,chunk_size=value['chunk_size'])
 def head(self,resource,minimum=0,allow_damaged=False):
  if not RESOURCE.fullmatch(resource):raise IntegrityError('Invalid resource identifier')
  rows=[];errors=[]
  for slot in range(2):
   try:row=json.loads(self.get('heads/'+resource+'/'+str(slot)))
   except Missing:continue
   except (IntegrityError,ValueError) as error:
    errors.append(error);continue
   if row.get('resource')!=resource or row.get('repository')!=self.id or type(row.get('sequence')) is not int or row['sequence']<1:raise IntegrityError('Invalid head identity/sequence')
   commit=self.manifest(row['commit'])
   if commit.get('resource')!=resource or commit.get('sequence')!=row['sequence'] or digest(encode(commit))!=row['sha256']:raise IntegrityError('Head references an invalid commit')
   rows.append(row)
  if errors and not allow_damaged:raise IntegrityError('Damaged head pointer; awaiting owned commit recovery or explicit restore selection')
  latest=max(rows,key=lambda x:x['sequence']) if rows else None
  if (latest['sequence'] if latest else 0)<minimum:raise IntegrityError('Remote head rollback or deletion detected')
  return latest
 def upload(self,resource,ident,source,journal,*,producer,parent=None,minimum=0,finish=lambda:None,check=lambda:None,chunk_size=CHUNK):
  identifier(ident)
  if not 1<=chunk_size<=CHUNK:raise ValueError('Invalid buffer limit')
  if parent is not None:identifier(parent)
  prepared=journal.prepared(ident)
  if prepared:
   if self.manifest(ident)!=prepared or prepared['producer']!=producer or prepared['parent']!=parent:raise IntegrityError('Prepared commit changed')
   head=self.head(resource,minimum,allow_damaged=True)
   if head and head['commit']==ident:journal.finish(ident);return prepared
   if (head['commit'] if head else None)!=prepared['previous'] or (head['sequence'] if head else 0)+1!=prepared['sequence']:raise IntegrityError('Concurrent writer or changed recovery head')
   self.publish(prepared,recovering=True);journal.finish(ident);return prepared
  head=self.head(resource,minimum)
  if head and head['commit']==ident:
   committed=self.manifest(ident)
   if committed['producer']!=producer or committed['parent']!=parent:raise IntegrityError('Committed stream identity changed')
   journal.finish(ident);return committed
  if parent is not None and (not head or parent!=head['commit']):raise IntegrityError('Incremental parent must be the current committed resource')
  identity={'repository':self.id,'resource':resource,'producer':producer,'parent':parent,'previous':head['commit'] if head else None,'chunk_size':chunk_size}
  journal.begin(ident,identity);rolling=hashlib.sha256();count=0;size=0
  while True:
   buffer=bytearray()
   while len(buffer)<chunk_size:
    check();block=source.read(min(1024**2,chunk_size-len(buffer)))
    if not block:break
    buffer.extend(block)
   if not buffer:break
   check();data=bytes(buffer);rolling.update(data);size+=len(data)
   if count>=MAX_PAGES*PAGE:raise IntegrityError('Stream exceeds manifest budget')
   old=journal.chunk(ident,count)
   if old:
    if old!=(len(data),digest(data)):raise IntegrityError('Regenerated stream differs from acknowledged prefix')
   else:
    self.put('chunks/'+ident+'/'+str(count),data);journal.acknowledged(ident,count,data)
   count+=1
  if not count:raise IntegrityError('Empty recovery stream refused')
  if journal.chunk(ident,count) is not None:raise IntegrityError('Regenerated stream was truncated')
  producer_result=finish();check();pages=[]
  for n,rows in enumerate(journal.pages(ident)):
   payload=encode(rows);name='pages/'+ident+'/'+str(n);self.put(name,payload);pages.append({'path':name,'sha256':digest(payload),'count':len(rows)})
  value={'format':1,'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'repository':self.id,'id':ident,'resource':resource,'sequence':(head['sequence'] if head else 0)+1,'previous':head['commit'] if head else None,'parent':parent,'producer':producer,'result':producer_result,'chunk_size':chunk_size,'bytes':size,'chunks':count,'sha256':rolling.hexdigest(),'pages':pages}
  self.put('commits/'+ident,encode(value));journal.prepared(ident,value);check()
  self.publish(value);journal.finish(ident);return value
 def publish(self,value,recovering=False):
  head=self.head(value['resource'],allow_damaged=recovering)
  if head and head['commit']==value['id']:return
  if (head['commit'] if head else None)!=value['previous'] or (head['sequence'] if head else 0)+1!=value['sequence']:raise IntegrityError('Recovery head changed during upload; refusing concurrent publication')
  pointer={'repository':self.id,'resource':value['resource'],'sequence':value['sequence'],'commit':value['id'],'sha256':digest(encode(value))}
  self.put('heads/'+value['resource']+'/'+str(value['sequence']%2),encode(pointer))
 def restore(self,ident,write,check=lambda:None):
  m=self.manifest(ident);rolling=hashlib.sha256();size=0;count=0
  if not 1<=len(m['pages'])<=MAX_PAGES:raise IntegrityError('Invalid manifest page count')
  for index,page in enumerate(m['pages']):
   check();name='pages/'+ident+'/'+str(index)
   if page['path']!=name:raise IntegrityError('Invalid page reference')
   payload=self.get(name)
   if digest(payload)!=page['sha256']:raise IntegrityError('Page hash mismatch')
   rows=json.loads(payload)
   if not 1<=len(rows)<=PAGE or len(rows)!=page['count']:raise IntegrityError('Invalid page size')
   for row in rows:
    check()
    if row['n']!=count or not 1<=row['size']<=CHUNK:raise IntegrityError('Invalid chunk sequence/size')
    data=self.get('chunks/'+ident+'/'+str(count),row['size'])
    if len(data)!=row['size'] or digest(data)!=row['sha256']:raise IntegrityError('Chunk hash mismatch')
    write(data);rolling.update(data);size+=len(data);count+=1
  if size!=m['bytes'] or count!=m['chunks'] or rolling.hexdigest()!=m['sha256']:raise IntegrityError('Complete stream hash mismatch')
  return m

# Callers choose explicit immutable commit IDs for restore. Native snapshots or
# database recovery are received into isolated targets by their resource adapter.
