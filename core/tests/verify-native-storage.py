#!/usr/bin/env python3
"""Identity/fallback contracts using mocked devices; never mounts real disks."""
import sys,stat,tempfile,unittest,json,types
from pathlib import Path
from unittest.mock import patch
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import nas_storage as s
U='12345678-1234-1234-1234-123456789abc';P='23456789-1234-1234-1234-123456789abc'
class Tests(unittest.TestCase):
 def setUp(self):
  self.c={'data':'/run/nas/storage/data','data_storage':{'driver':'btrfs','mount':'/run/nas/storage','subvolume':'data','uuid':U,'partuuid':P,'partition_bytes':2048}}
  self.row={'source':'/dev/sdz2','fstype':'btrfs','uuid':U,'fsroot':'/','options':'rw,nosuid,nodev'}
 def test_legacy_does_not_activate_native_adapter(self):self.assertFalse(s.configured({'root':'/mnt/d/nas'}))
 def test_unknown_driver_cannot_fall_back_to_ntfs(self):
  with self.assertRaises(RuntimeError):s.configured({'data_storage':{'driver':'unknown'}})
 def test_declared_native_paths(self):self.assertEqual(s.specification(self.c)[1],Path('/run/nas/storage'))
 def test_invalid_uuid_refused(self):
  self.c['data_storage']['uuid']='../../wrong'
  with self.assertRaises(RuntimeError):s.specification(self.c)
 def test_foreign_mount_location_refused(self):
  self.c['data_storage']['mount']='/mnt/f'
  with self.assertRaises(RuntimeError):s.specification(self.c)
 def test_wrong_active_path_refused(self):
  self.c['data']='/run/nas/storage/other'
  with self.assertRaises(RuntimeError):s.specification(self.c)
 def test_missing_mount_never_falls_back(self):
  with patch.object(s,'is_mounted',return_value=False),patch.object(s,'output') as output:
   with self.assertRaisesRegex(RuntimeError,'fallback'):s.verify(self.c)
   output.assert_not_called()
 def check_row(self,row,writable=True):
  with patch.object(s,'is_mounted',return_value=True),patch.object(s,'output',return_value=json.dumps({'filesystems':[row]})),patch.object(s,'device',return_value=Path('/dev/sdz2')),patch.object(Path,'is_symlink',return_value=False),patch.object(Path,'is_dir',return_value=True),patch.object(Path,'stat',return_value=types.SimpleNamespace(st_ino=256)):
   return s.verify(self.c,writable=writable)
 def test_wrong_filesystem_refused(self):
  with self.assertRaises(RuntimeError):self.check_row({**self.row,'fstype':'ext4'})
 def test_wrong_uuid_refused(self):
  with self.assertRaises(RuntimeError):self.check_row({**self.row,'uuid':P})
 def test_wrong_subvolume_root_refused(self):
  with self.assertRaises(RuntimeError):self.check_row({**self.row,'fsroot':'/other'})
 def test_readonly_data_refused_for_writes(self):
  with self.assertRaisesRegex(RuntimeError,'read-only'):self.check_row({**self.row,'options':'ro,nodev'})
 def test_readonly_mount_can_be_verified_for_clean_unmount(self):self.check_row({**self.row,'options':'ro,nodev'},writable=False)
 def test_identity_verified_mount_accepted(self):self.assertEqual(self.check_row(self.row)['uuid'],U)
 def test_changed_partition_identity_is_refused(self):
  with patch.object(Path,'exists',return_value=True),patch.object(Path,'stat',return_value=types.SimpleNamespace(st_mode=stat.S_IFBLK)),patch.object(Path,'resolve',return_value=Path('/dev/sdz2')),patch.object(s,'output',side_effect=['btrfs',P]):
   with self.assertRaisesRegex(RuntimeError,'identity mismatch'):s.device(self.c['data_storage'])
 def test_loop_image_is_not_a_native_partition(self):
  with patch.object(Path,'exists',return_value=True),patch.object(Path,'stat',return_value=types.SimpleNamespace(st_mode=stat.S_IFBLK)),patch.object(Path,'resolve',return_value=Path('/dev/loop9')):
   with self.assertRaisesRegex(RuntimeError,'loop image'):s.device(self.c['data_storage'])
 def test_partition_size_mismatch_is_refused(self):
  with patch.object(Path,'exists',return_value=True),patch.object(Path,'stat',return_value=types.SimpleNamespace(st_mode=stat.S_IFBLK)),patch.object(Path,'resolve',return_value=Path('/dev/sdz2')),patch.object(s,'output',side_effect=['btrfs',U,P,'4096']):
   with self.assertRaisesRegex(RuntimeError,'size mismatch'):s.device(self.c['data_storage'])
if __name__=='__main__':unittest.main(verbosity=2)
