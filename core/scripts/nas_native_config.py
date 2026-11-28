"""Engine selection without importing optional native backup dependencies."""
ENGINE='btrfs-v1'

def enabled(c):
 engine=c.get('cloud_backup_engine','restic-online')
 if engine not in ('restic-online',ENGINE):raise RuntimeError('Unsupported configured cloud backup engine')
 return engine==ENGINE
