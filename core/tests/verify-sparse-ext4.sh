#!/usr/bin/env bash
# Formats only the exact previously verified blank sparse test file; never production data.
set -euo pipefail
# Host-specific continuation for the exact blank sparse experiment verified on 2026-11-15.
[[ $# == 0 ]] || { echo 'No arguments accepted.' >&2; exit 2; }
mode=--mount
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
for tool in mktemp truncate mkfs.ext4 e2fsck debugfs sha256sum stat; do
  command -v "$tool" >/dev/null || { echo "Missing: $tool" >&2; exit 1; }
done
if [[ $mode == --mount ]]; then
  for tool in mount umount mountpoint findmnt losetup runuser; do command -v "$tool" >/dev/null; done
  [[ $EUID == 0 && -c /dev/loop-control ]] || {
    echo 'BLOCKED: requires root, CAP_SYS_ADMIN and loop devices in the target host.' >&2; exit 1;
  }
  python3 - <<'PY'
from pathlib import Path
s = Path('/proc/self/status').read_text()
cap = int(next(x.split()[1] for x in s.splitlines() if x.startswith('CapEff:')), 16)
if not cap & (1 << 21):
    raise SystemExit('BLOCKED: CAP_SYS_ADMIN unavailable')
PY
fi
work=$root/system/runtime/storage-probes/sparse-probe-fb57b053d85447d4b6e0a52ca297471d
image=$work/blank.img
[[ -f $image && ! -L $image && ! -L $work ]] || { echo 'Expected isolated blank file missing or symlink.' >&2; exit 1; }
command -v flock >/dev/null
exec 9<> "$image"
flock -n 9 || { echo 'Image is in use by another experiment.' >&2; exit 1; }
[[ -z $(losetup -j "$image") ]] || { echo 'Image already attached; refusing format.' >&2; exit 1; }
# Refuse all nonzero content, including an already formatted filesystem.
python3 - "$image" <<'PYZERO'
import sys
from pathlib import Path
p = Path(sys.argv[1])
if p.stat().st_size != 268435456:
    raise SystemExit('Unexpected logical size')
with p.open('rb') as f:
    while chunk := f.read(1024 * 1024):
        if any(chunk):
            raise SystemExit('File is not completely blank; refusing format or repeat run')
print('Verified exact isolated file: 256 MiB, all bytes zero.')
PYZERO
[[ $(stat -c %b "$image") == 0 ]] || {
  echo 'Expected zero allocated blocks before format; refusing experiment.' >&2; exit 1;
}
printf 'Artifacts retained: %s\n' "$work"
exec > >(tee "$work/mount-result.log") 2>&1
loop=''
point=''
cleanup() {
  local result=$?
  trap - EXIT
  if [[ -n $point ]] && mountpoint -q "$point"; then
    if ! umount -- "$point"; then
      echo "UNSAFE TO EJECT: mount remains at $point" >&2
      exit 1
    fi
  fi
  if [[ -n $loop ]]; then
    if ! losetup -d "$loop"; then echo "UNSAFE TO EJECT: $loop remains attached" >&2; exit 1; fi
  fi
  exit "$result"
}
trap cleanup EXIT
stat -c 'Before format: logical=%s bytes allocated=%b*512 bytes' "$image"
mkfs.ext4 -q -L NAS_PROBE -E lazy_itable_init=0,lazy_journal_init=0 "$image"
printf 'NAS isolated persistence probe\n' > "$work/payload"
expected=$(sha256sum "$work/payload" | cut -d ' ' -f 1)
if [[ $mode == --offline ]]; then
  debugfs -w -R "write $work/payload /payload" "$image"
  debugfs -R 'cat /payload' "$image" > "$work/readback"
  [[ $(sha256sum "$work/readback" | cut -d ' ' -f 1) == "$expected" ]]
  e2fsck -fn "$image"
  echo 'PASS: fresh raw ext4 image, offline write/read and filesystem check.'
  echo 'NOT TESTED: mount/unmount, POSIX permissions, Docker/PostgreSQL, Windows/Linux handoff.'
else
  point=$(mktemp -d /tmp/nas-probe-mount-XXXXXXXX)
  loop=$(losetup --find --show --nooverlap "$image")
  mount -t ext4 -o nodev,nosuid,noexec "$loop" "$point"
  [[ $(findmnt -n -o FSTYPE --target "$point") == ext4 ]]
  [[ $(findmnt -n -o SOURCE --target "$point") == "$loop" ]]
  install -d -m 700 -o 65534 -g 65534 "$point/permission-test"
  runuser -u nobody -- sh -c 'umask 077; printf "unprivileged write\n" > "$1/file"' sh "$point/permission-test"
  [[ $(stat -c '%u:%g:%a' "$point/permission-test/file") == 65534:65534:600 ]]
  cp -- "$work/payload" "$point/payload"
  sync -f "$point/payload"
  umount -- "$point"
  losetup -d "$loop"; loop=''
  e2fsck -fn "$image"
  loop=$(losetup --find --show --nooverlap "$image")
  mount -t ext4 -o nodev,nosuid,noexec "$loop" "$point"
  [[ $(sha256sum "$point/payload" | cut -d ' ' -f 1) == "$expected" ]]
  [[ $(stat -c '%u:%g:%a' "$point/permission-test/file") == 65534:65534:600 ]]
  umount -- "$point"
  losetup -d "$loop"; loop=''
  rmdir -- "$point"; point=''
  e2fsck -fn "$image"
  echo 'PASS: isolated mount/write/permissions/unmount/reattach/persistence/unmount.'
  echo 'NOT TESTED: Docker/PostgreSQL, physical volume identity, Windows/Linux handoff.'
fi
stat -c 'After test: logical=%s bytes allocated=%b*512 bytes' "$image"
logical=$(stat -c %s "$image")
allocated=$(( $(stat -c %b "$image") * 512 ))
if (( allocated < logical )); then
  echo 'Allocation report suggests sparse storage; host physical allocation still needs verification.'
else
  echo 'CAPACITY GATE FAILED: allocation is not sparse according to this filesystem; not suitable for production yet.'
fi
