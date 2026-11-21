#!/usr/bin/env bash
# Destructive only to a proven blank, fresh postgres-probe image. No production paths.
set -euo pipefail
[[ $EUID == 0 && $# == 1 ]] || { echo 'Usage: sudo bash verify-postgres-storage.sh <new-test-image>'; exit 2; }
daemon_pid=$(pidof dockerd)
[[ $daemon_pid =~ ^[0-9]+$ && $(readlink /proc/self/ns/mnt) == "$(readlink "/proc/$daemon_pid/ns/mnt")" ]] || {
  echo 'Mount namespace differs from Docker. Run with sudo nsenter -t <dockerd-pid> -m -- bash <script> <new-image>.'; exit 1;
}
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
image=$(realpath -e -- "$1")
work=$(dirname -- "$image")
[[ $work =~ ^$root/system/runtime/storage-probes/postgres-probe-[a-f0-9]{32}$ && $image == "$work/database.img" && ! -L $1 ]] || { echo 'Not an isolated postgres-probe image.'; exit 1; }
[[ -f $image && $(stat -c %s "$image") == 2147483648 && $(stat -c %b "$image") == 0 ]] || { echo 'Image not pristine/blank/sparse; refusing format or repeat run.'; exit 1; }
exec 9<> "$image"
flock -n 9
[[ -z $(losetup -j "$image") && ! -e $work/result.log ]]
(( $(df --output=avail -B1 "$work" | tail -n 1) > 8589934592 ))
d=(/usr/bin/docker --host unix:///var/run/docker.sock)
ref='ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0@sha256:bcf63357191b76a916ae5eb93464d65c07511da41e3bf7a8416db519b40b1c23'
"${d[@]}" image inspect "$ref" >/dev/null
python3 - "$image" <<'PY'
import sys
with open(sys.argv[1], 'rb') as f:
    while chunk := f.read(8 * 1024 * 1024):
        if any(chunk): raise SystemExit('Nonzero image; refusing format')
PY
exec > >(tee "$work/result.log") 2>&1
name=nas-pg-probe-$(basename "$work" | cut -c 16-27)
point=$(mktemp -d /tmp/nas-pg-probe-XXXXXXXX)
loop=''
created=0
cleanup() {
  result=$?
  trap - EXIT
  if (( created )); then
    if [[ $("${d[@]}" inspect -f '{{.State.Running}}' "$name") == true ]]; then
      "${d[@]}" stop --time 60 "$name" || { echo 'UNSAFE: container stop failed; mount retained'; exit 1; }
    fi
    [[ $("${d[@]}" inspect -f '{{.State.ExitCode}}' "$name") == 0 ]] || { echo 'UNSAFE: unclean database exit; mount retained'; exit 1; }
    "${d[@]}" logs "$name" > "$work/container.log" 2>&1
    "${d[@]}" rm "$name" || { echo 'UNSAFE: container removal failed; mount retained'; exit 1; }
  fi
  if mountpoint -q "$point"; then umount "$point" || { echo 'UNSAFE: mount retained'; exit 1; }; fi
  if [[ -n $loop ]]; then losetup -d "$loop" || exit 1; fi
  rmdir "$point"
  exit "$result"
}
trap cleanup EXIT
"${d[@]}" image inspect "$ref" --format 'Image={{.Id}} Architecture={{.Architecture}} Digests={{json .RepoDigests}}'
mkfs.ext4 -q -L NAS_PG_PROBE -E lazy_itable_init=0,lazy_journal_init=0 "$image"
attach() {
  loop=$(losetup --find --show --nooverlap "$image")
  mount -t ext4 -o nodev,nosuid,noexec "$loop" "$point"
  [[ $(findmnt -n -o FSTYPE --target "$point") == ext4 && $(findmnt -n -o SOURCE --target "$point") == "$loop" ]]
}
detach() {
  umount "$point"
  losetup -d "$loop"; loop=''
  e2fsck -fn "$image"
}
attach
install -d -m 700 "$point/postgres"
# Probe only, isolated network and no published ports. Trust applies only to this disposable test.
"${d[@]}" create --name "$name" --label nas.purpose=storage-probe --network none --restart no \
  --memory 512m --shm-size 128m --mount "type=bind,source=$point/postgres,target=/var/lib/postgresql/data" \
  -e POSTGRES_USER=postgres -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=nas_probe "$ref" >/dev/null
created=1
ready() {
  for ((i=0;i<90;i++)); do
    if "${d[@]}" exec "$name" pg_isready -h 127.0.0.1 -U postgres -d nas_probe >/dev/null 2>&1; then return; fi
    sleep 1
  done
  echo 'Database readiness timed out'; return 1
}
sql() { "${d[@]}" exec -i "$name" psql -X -v ON_ERROR_STOP=1 -U postgres -d nas_probe "$@"; }
"${d[@]}" start "$name" >/dev/null
ready
stat -c 'PGDATA ownership=%u:%g mode=%a' "$point/postgres"
[[ $(stat -c %a "$point/postgres") == 700 ]]
blkid "$loop"
sql <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS vchord CASCADE;
CREATE TABLE persistence_probe (id integer PRIMARY KEY, payload text NOT NULL);
INSERT INTO persistence_probe SELECT i, repeat(md5(i::text), 32) FROM generate_series(1,10000) i;
CHECKPOINT;
SELECT version();
SELECT extname, extversion FROM pg_extension ORDER BY extname;
SQL
sql -Atc "SELECT count(*) || ':' || md5(string_agg(id::text || ':' || payload, ',' ORDER BY id)) FROM persistence_probe" > "$work/expected.txt"
"${d[@]}" exec "$name" pg_dump -U postgres -d nas_probe -Fc > "$work/probe.dump"
"${d[@]}" stop --time 60 "$name"
[[ $("${d[@]}" inspect -f '{{.State.ExitCode}}' "$name") == 0 ]]
detach
attach
"${d[@]}" start "$name" >/dev/null
ready
sql -Atc "SELECT count(*) || ':' || md5(string_agg(id::text || ':' || payload, ',' ORDER BY id)) FROM persistence_probe" > "$work/actual.txt"
cmp "$work/expected.txt" "$work/actual.txt"
"${d[@]}" exec "$name" createdb -U postgres nas_restore
"${d[@]}" exec -i "$name" pg_restore --exit-on-error -U postgres -d nas_restore < "$work/probe.dump"
"${d[@]}" exec "$name" psql -X -At -v ON_ERROR_STOP=1 -U postgres -d nas_restore -c "SELECT count(*) || ':' || md5(string_agg(id::text || ':' || payload, ',' ORDER BY id)) FROM persistence_probe" > "$work/restored.txt"
cmp "$work/expected.txt" "$work/restored.txt"
"${d[@]}" stop --time 60 "$name"
[[ $("${d[@]}" inspect -f '{{.State.ExitCode}}' "$name") == 0 ]]
detach
stat -c 'Image logical=%s allocated=%b*512 bytes' "$image"
(( $(stat -c %b "$image") * 512 < $(stat -c %s "$image") ))
echo 'PASS: isolated PostgreSQL write, clean stop, unmount/reattach, row checksum and logical backup/restore.'
echo 'NOT TESTED: production Immich, prolonged load, disk-full behavior or cross-host handoff.'
