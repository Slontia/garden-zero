"""Identity-checked native Btrfs data mounts; never formats or repairs a disk."""
import json,re,stat,subprocess
from pathlib import Path

UUID=re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')

def configured(c):
    value=c.get('data_storage',{})
    if value and value.get('driver')!='btrfs':raise RuntimeError('Unsupported declared data storage driver')
    return value.get('driver')=='btrfs'

def specification(c):
    s=c.get('data_storage',{})
    if s.get('driver')!='btrfs':raise RuntimeError('Unsupported native data storage driver')
    for key in ('uuid','partuuid'):
        if not isinstance(s.get(key),str) or not UUID.fullmatch(s[key]):raise RuntimeError('Invalid native data '+key)
    mount=Path(s['mount']);sub=s.get('subvolume','data')
    if mount.parent!=Path('/run/nas') or mount.name!='storage' or not re.fullmatch(r'[a-z][a-z0-9_-]*',sub):raise RuntimeError('Unsafe native data mount configuration')
    if Path(c['data'])!=mount/sub:raise RuntimeError('Native data path does not match registered subvolume')
    if not isinstance(s.get('partition_bytes'),int) or s['partition_bytes']<=0:raise RuntimeError('Native data partition size missing')
    return s,mount

def output(args):return subprocess.check_output([str(x) for x in args],text=True,stderr=subprocess.DEVNULL,timeout=20).strip()

def device(s):
    path=Path('/dev/disk/by-partuuid')/s['partuuid']
    if not path.exists() or not stat.S_ISBLK(path.stat().st_mode):raise RuntimeError('Native NAS partition is not attached')
    actual=path.resolve()
    if actual.name.startswith('loop'):raise RuntimeError('Native NAS data cannot use a loop image')
    expected={'TYPE':'btrfs','UUID':s['uuid'],'PARTUUID':s['partuuid']}
    for key,value in expected.items():
        if output(['blkid','-s',key,'-o','value',actual]).lower()!=value:raise RuntimeError('Native NAS partition identity mismatch: '+key)
    if int(output(['blockdev','--getsize64',actual]))!=s['partition_bytes']:raise RuntimeError('Native NAS partition size mismatch')
    return actual

def is_mounted(mount):return subprocess.run(['mountpoint','-q',str(mount)]).returncode==0

def verify(c,*,writable=True):
    s,mount=specification(c)
    if not is_mounted(mount):raise RuntimeError('Native NAS data mount missing; refusing empty-directory fallback')
    rows=json.loads(output(['findmnt','-J','--mountpoint',mount,'-o','SOURCE,FSTYPE,UUID,FSROOT,OPTIONS']))['filesystems']
    if len(rows)!=1:raise RuntimeError('Ambiguous native NAS mount')
    row=rows[0]
    if row.get('fstype')!='btrfs' or row.get('uuid','').lower()!=s['uuid'] or row.get('fsroot')!='/':raise RuntimeError('Native NAS mounted filesystem identity mismatch')
    if Path(row['source'].split('[')[0]).resolve()!=device(s):raise RuntimeError('Native NAS mounted partition mismatch')
    if writable and 'rw' not in row.get('options','').split(','):raise RuntimeError('Native NAS data filesystem is read-only')
    data=Path(c['data'])
    if mount.is_symlink() or data.is_symlink() or not data.is_dir() or data.stat().st_ino!=256:raise RuntimeError('Native NAS active subvolume missing or linked')
    return row

def mount_data(c):
    s,mount=specification(c)
    if is_mounted(mount):return verify(c)
    actual=device(s)
    if mount.is_symlink() or (mount.exists() and any(mount.iterdir())):raise RuntimeError('Native NAS mountpoint is linked or nonempty')
    mount.mkdir(mode=0o750,parents=True,exist_ok=True)
    subprocess.run(['mount','-t','btrfs','-o','subvolid=5,nosuid,nodev',str(actual),str(mount)],check=True)
    return verify(c)

def unmount_data(c):
    _,mount=specification(c)
    if is_mounted(mount):
        verify(c,writable=False)
        subprocess.run(['umount',str(mount)],check=True)
