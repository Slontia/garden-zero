#!/usr/bin/env python3
"""Protocol fixtures only; no database connection or production writes."""
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import io,json,struct,tarfile,tempfile,unittest
from nas_pg_stream import Frames,WalWriter,BaseManifestTap,Gap,IntegrityError,lsn,position

class Tests(unittest.TestCase):
 def test_lsn_validation_and_round_trip(self):
  for n in [0,1,2**32+123,2**64-1]:self.assertEqual(lsn(position(n)),n)
  for value in ['../x','0/-1','1/FFFFFFFFF',-1]:
   with self.assertRaises(IntegrityError):lsn(value)
 def test_frame_bytes_are_independent_of_message_boundaries(self):
  data=bytes(range(256))*600
  a=Frames(0,len(data));whole=b''.join(a.feed(0,data))
  b=Frames(0,len(data));split=[]
  for offset in range(0,len(data),999):split.extend(b.feed(offset,data[offset:offset+999]))
  self.assertEqual(whole,b''.join(split))
 def test_gap_is_refused_and_overlap_is_trimmed(self):
  f=Frames(100,200)
  with self.assertRaises(Gap):f.feed(101,b'data')
  self.assertEqual(f.feed(50,b'x'*100),[])
  self.assertEqual(f.next,150);f.feed(50,b'x'*100);self.assertEqual(f.next,150)
  self.assertTrue(f.feed(150,b'x'*100));self.assertEqual(f.next,200)
 def test_write_segment_boundary_and_incomplete_input(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);segment=1024**2;(root/'000000010000000000000000').write_bytes(bytes(segment))
   f=Frames(segment-10,segment+100);blob=b''.join(f.feed(segment-10,b'a'*110));writer=WalWriter(root,segment-10,segment+100,1,segment)
   for i in range(0,len(blob),7):writer.write(blob[i:i+7])
   writer.finish();self.assertEqual((root/'000000010000000000000000').read_bytes()[-10:],b'a'*10);self.assertEqual((root/'000000010000000000000001').read_bytes()[:100],b'a'*100)
   with self.assertRaises(Gap):WalWriter(root,0,20,1,segment).finish()
 def test_missing_prefix_and_symlink_refused(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);writer=WalWriter(root,5,10,1,1024**2)
   with self.assertRaises(Gap):writer.write(b''.join(Frames(5,10).feed(5,b'abcde')))
   (root/'000000010000000000000000').symlink_to(root/'outside')
   with self.assertRaises(OSError):WalWriter(root,0,5,1,1024**2).write(b''.join(Frames(0,5).feed(0,b'abcde')))
 def manifest(self):
  buf=io.BytesIO()
  with tarfile.open(fileobj=buf,mode='w') as tar:
   for name,data in [('backup_label',b'START TIMELINE: 1\n'),('backup_manifest',json.dumps({'Files':[{'Path':'fixture'}]*10000,'WAL-Ranges':[{'Timeline':1,'Start-LSN':'0/100','End-LSN':'0/200'}],'Manifest-Checksum':'fixture'}).encode())]:
    info=tarfile.TarInfo(name);info.size=len(data);tar.addfile(info,io.BytesIO(data))
  return buf.getvalue()
 def test_large_manifest_is_tapped_in_constant_memory(self):
  tap=BaseManifestTap();data=self.manifest()
  for offset in range(0,len(data),333):tap.feed(data[offset:offset+333]);self.assertLessEqual(len(tap.tail),65536)
  self.assertEqual(tap.finish()['end_lsn'],512)
 def test_postgres14_partial_zero_terminator_is_accepted(self):
  tap=BaseManifestTap();tap.feed(self.manifest()[:-17]);self.assertEqual(tap.finish()['end_lsn'],512)
 def test_truncated_and_corrupt_tar_refused(self):
  tap=BaseManifestTap();data=self.manifest();tap.feed(data[:100])
  with self.assertRaises(IntegrityError):tap.finish()
  data=bytearray(data);data[4]^=1
  with self.assertRaises(IntegrityError):BaseManifestTap().feed(data)

if __name__=='__main__':unittest.main()
