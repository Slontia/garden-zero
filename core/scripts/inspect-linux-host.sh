#!/usr/bin/env bash
# Read-only preflight for a Linux-native Engine, excluding Windows Desktop shims.
set -u
printf 'Linux distribution\n'; cat /etc/os-release
printf '\nKernel and architecture\n'; uname -r; uname -m
printf '\nInit process\n'; ps -p 1 -o comm=
printf '\nMemory\n'; free -m
printf '\nNative Docker binaries\n'
for binary in /usr/bin/docker /usr/local/bin/docker /usr/bin/dockerd /usr/local/bin/dockerd; do
  if [[ -e $binary ]]; then ls -l "$binary"; fi
done
printf '\nInstalled runtime packages\n'
dpkg-query -W -f='${binary:Package} ${Version} ${db:Status-Status}\n' docker-ce docker-ce-cli docker.io docker-compose-plugin docker-compose-v2 containerd containerd.io runc podman-docker 2>/dev/null || true
printf '\nSystemd runtime service states\n'
if [[ $(ps -p 1 -o comm=) == systemd ]]; then
  systemctl is-active docker containerd || true
  systemctl is-enabled docker containerd || true
fi
printf '\nLocal Engine socket\n'; ls -l /var/run/docker.sock 2>/dev/null || true
printf '\nLinux filesystem locations\n'
findmnt -T /var/lib/docker
findmnt -T /mnt/d/nas
printf '\nLocal Engine inventory\n'
native=''
for candidate in /usr/bin/docker /usr/local/bin/docker; do
  if [[ -x $candidate ]]; then
    resolved=$(readlink -f "$candidate")
    case "$resolved" in /mnt/*) continue ;; esac
    native=$candidate; break
  fi
done
if [[ -n $native && -S /var/run/docker.sock ]]; then
  # Explicit local endpoint: never select a Desktop or remote context implicitly.
  "$native" --host unix:///var/run/docker.sock version
  "$native" --host unix:///var/run/docker.sock ps -a --format '{{.Names}} | {{.Image}} | {{.Status}}'
  "$native" --host unix:///var/run/docker.sock volume ls
else
  echo 'Native Linux CLI and local socket not both available; no engine changes made.'
fi
printf '\nPreflight complete. Command errors above are unresolved checks.\n'
