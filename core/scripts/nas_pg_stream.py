"""Bounded PostgreSQL physical WAL fragments; no slot or synchronous standby.

SQL/provisioning belongs to the shared database resource adapter. This stream
never asks the source to retain WAL: gaps invalidate the chain and require a new
base, rather than allowing a slow cloud to fill the database disk.
"""
import collections,io,json,queue,re,select,struct,threading,time
from pathlib import Path
from nas_stream import IntegrityError

FRAME=64*1024
HEADER=struct.Struct('!QI')
class Gap(IntegrityError):pass

def lsn(value):
 if isinstance(value,int) and 0<=value<2**64:return value
 if not isinstance(value,str) or not re.fullmatch(r'[0-9A-Fa-f]{1,8}/[0-9A-Fa-f]{1,8}',value):raise IntegrityError('Invalid WAL position')
 a,b=value.split('/');return (int(a,16)<<32)|int(b,16)
def position(value):return '%X/%X'%(lsn(value)>>32,lsn(value)&0xffffffff)

class Frames:
 """Normalize arbitrary network message boundaries for deterministic retry."""
 def __init__(self,start,end):
  self.begin=lsn(start);self.end=lsn(end);self.next=self.begin;self.pending=bytearray();self.offset=self.begin
  if self.end<=self.begin:raise IntegrityError('Empty/invalid WAL range')
 def feed(self,start,data):
  start=lsn(start)
  if len(data)>1024**2:raise IntegrityError('Replication message exceeded its memory budget')
  if start>self.next:raise Gap('WAL continuity gap; a new physical base is required')
  if start+len(data)<=self.next:return []
  data=data[self.next-start:self.end-start];self.next+=len(data);self.pending.extend(data);result=[]
  while len(self.pending)>=FRAME:
   block=bytes(self.pending[:FRAME]);del self.pending[:FRAME];result.append(HEADER.pack(self.offset,len(block))+block);self.offset+=len(block)
  if self.next==self.end and self.pending:
   block=bytes(self.pending);self.pending.clear();result.append(HEADER.pack(self.offset,len(block))+block);self.offset+=len(block)
  return result

class WalStream:
 def __init__(self,connection,start,end,*,timeline,system_identifier,check=lambda:None,timeout=60):
  self.connection_factory=connection;self.start=lsn(start);self.end=lsn(end);self.timeline=timeline;self.system=str(system_identifier);self.check=check;self.timeout=timeout
  self.queue=queue.Queue(maxsize=256);self.stop=threading.Event();self.done=threading.Event();self.error=None;self.buffer=b''
  self.thread=threading.Thread(target=self.receive,daemon=True);self.thread.start()
 def receive(self):
  connection=None
  try:
   connection=self.connection_factory();cursor=connection.cursor();cursor.execute('IDENTIFY_SYSTEM');row=cursor.fetchone()
   if str(row[0])!=self.system or int(row[1])!=self.timeline:raise Gap('Database system/timeline changed; a new base is required')
   cursor.start_replication(start_lsn=self.start,timeline=self.timeline,status_interval=5)
   frames=Frames(self.start,self.end);last=time.monotonic()
   while not self.stop.is_set() and frames.next<frames.end:
    message=cursor.read_message()
    if message is None:
     if time.monotonic()-last>self.timeout:raise Gap('WAL source did not supply the requested range; retry or rebuild the base')
     select.select([connection],[],[],.5);continue
    before=frames.next;blocks=frames.feed(message.data_start,bytes(message.payload))
    if frames.next>before:last=time.monotonic()
    for block in blocks:
     while not self.stop.is_set():
      try:self.queue.put(block,timeout=.5);break
      except queue.Full:
       # No slot and no synchronous registration. Feedback never claims that
       # uncommitted cloud bytes are durably flushed/applied.
       cursor.send_feedback(write_lsn=frames.next,flush_lsn=self.start,reply=True)
   if not self.stop.is_set() and frames.next!=self.end:raise Gap('Incomplete WAL range')
  except Exception as error:
   self.error=Gap('Required WAL segment was recycled; a new physical base is required') if 'requested WAL segment' in str(error) and 'removed' in str(error) else error
  finally:
   if connection:connection.close()
   self.done.set()
 def read(self,n):
  if n<1:raise ValueError('Bounded WAL reads required')
  self.check()
  if self.buffer:
   data=self.buffer[:n];self.buffer=self.buffer[n:];return data
  while True:
   self.check()
   try:block=self.queue.get(timeout=.25);self.buffer=block[n:];return block[:n]
   except queue.Empty:
    if self.done.is_set():
     if self.error:raise self.error
     return b''
 def finish(self):
  self.thread.join(timeout=5)
  if not self.done.is_set() or self.error:raise Gap('WAL producer did not complete; recovery point not published')
 def close(self):self.stop.set();self.thread.join(timeout=10)
 def __enter__(self):return self
 def __exit__(self,*error):self.close()

class WalWriter:
 """Apply authenticated fragments only into an isolated restored base's pg_wal."""
 def __init__(self,directory,start,end,timeline,segment_size=16*1024**2):
  self.directory=Path(directory);self.next=lsn(start);self.end=lsn(end);self.timeline=timeline;self.segment=segment_size;self.buffer=bytearray()
  if not self.directory.is_dir() or self.directory.is_symlink() or segment_size&(segment_size-1) or not 1024**2<=segment_size<=1024**3:raise IntegrityError('Unsafe restore WAL directory or segment size')
 def write(self,data):
  import os,stat
  self.buffer.extend(data)
  while len(self.buffer)>=HEADER.size:
   start,size=HEADER.unpack(self.buffer[:HEADER.size])
   if start!=self.next or not 1<=size<=FRAME or start+size>self.end:raise IntegrityError('Invalid/noncontiguous WAL frame')
   if len(self.buffer)<HEADER.size+size:break
   payload=bytes(self.buffer[HEADER.size:HEADER.size+size]);del self.buffer[:HEADER.size+size]
   offset=0
   while offset<len(payload):
    number=self.next//self.segment;within=self.next%self.segment;per_log=2**32//self.segment;name='%08X%08X%08X'%(self.timeline,number//per_log,number%per_log);file=self.directory/name
    if not file.exists() and within:raise Gap('Required WAL segment prefix is missing from the restored base')
    fd=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
     info=os.fstat(fd)
     if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise IntegrityError('Unsafe restored WAL file')
     if info.st_size not in (0,self.segment):raise IntegrityError('Invalid restored WAL segment size')
     if info.st_size==0:
      owner=self.directory.stat();os.fchown(fd,owner.st_uid,owner.st_gid);os.ftruncate(fd,self.segment)
     amount=min(len(payload)-offset,self.segment-within);written=os.pwrite(fd,payload[offset:offset+amount],within)
     if written!=amount:raise IOError('Short WAL restore write')
    finally:os.close(fd)
    self.next+=amount;offset+=amount
 def finish(self):
  if self.buffer or self.next!=self.end:raise Gap('Incomplete restored WAL stream')

class BaseManifestTap:
 """Inspect only TAR headers and a fixed manifest tail while forwarding bytes."""
 def __init__(self):
  self.buffer=bytearray();self.remaining=0;self.padding=0;self.name=None;self.tail=bytearray();self.label=None;self.ranges=None;self.ended=False
 def feed(self,data):
  cursor=0
  while cursor<len(data):
   if self.remaining:
    size=min(self.remaining,len(data)-cursor)
    if self.name in ('backup_manifest','backup_label'):
     self.tail.extend(data[cursor:cursor+size])
     if len(self.tail)>65536:del self.tail[:-65536]
    cursor+=size;self.remaining-=size
    if not self.remaining:
     if self.name=='backup_manifest':
      match=re.search(rb'"WAL-Ranges"\s*:\s*',self.tail)
      if not match:raise IntegrityError('Physical backup manifest lacks bounded WAL range metadata')
      ranges,_=json.JSONDecoder().raw_decode(bytes(self.tail[match.end():]).decode().lstrip())
      if len(ranges)!=1:raise Gap('Timeline changed during the physical base; rebuild required')
      row=ranges[0]
      if type(row['Timeline']) is not int or row['Timeline']<1 or lsn(row['Start-LSN'])>=lsn(row['End-LSN']):raise IntegrityError('Invalid physical backup WAL range')
      self.ranges=row
     elif self.name=='backup_label':self.label=bytes(self.tail)
     self.tail.clear()
    continue
   if self.padding:
    size=min(self.padding,len(data)-cursor);cursor+=size;self.padding-=size;continue
   take=min(512-len(self.buffer),len(data)-cursor);self.buffer.extend(data[cursor:cursor+take]);cursor+=take
   if len(self.buffer)<512:continue
   header=bytes(self.buffer);self.buffer.clear()
   if header==bytes(512):self.ended=True;continue
   if self.ended:raise IntegrityError('Unexpected data after physical backup TAR terminator')
   checksum=int(header[148:156].rstrip(b'\x00 ').lstrip(b' ') or b'0',8)
   if sum(header[:148])+8*32+sum(header[156:])!=checksum:raise IntegrityError('Physical backup TAR header checksum failed')
   name=header[:100].split(b'\x00')[0];prefix=header[345:500].split(b'\x00')[0]
   if prefix:name=prefix+b'/'+name
   self.name=name.decode().removeprefix('./');self.remaining=int(header[124:136].rstrip(b'\x00 ').lstrip(b' ') or b'0',8);self.padding=(-self.remaining)%512
 def finish(self):
  if self.remaining or self.padding or any(self.buffer) or not self.ended or not self.ranges or not self.label:raise IntegrityError('Physical backup TAR/manifest incomplete: '+repr({'remaining':self.remaining,'padding':self.padding,'header_bytes':len(self.buffer),'terminated':self.ended,'wal_ranges':bool(self.ranges),'label':bool(self.label),'last_member':self.name}))
  match=re.search(rb'START TIMELINE:\s*(\d+)',self.label)
  if not match or int(match[1])!=self.ranges['Timeline']:raise IntegrityError('Physical backup timeline mismatch')
  return {'timeline':self.ranges['Timeline'],'start_lsn':lsn(self.ranges['Start-LSN']),'end_lsn':lsn(self.ranges['End-LSN'])}

class TappedBase:
 def __init__(self,producer):self.producer=producer;self.tap=BaseManifestTap();self.metadata=None
 def read(self,n):
  data=self.producer.read(n)
  if data:self.tap.feed(data)
  return data
 def finish(self):
  self.producer.finish();self.metadata=self.tap.finish();return self.metadata
