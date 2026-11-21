#!/usr/bin/env bash
# Read-only inventory: does not start services, pull images or print container env.
set -u
printf 'OS and kernel\n'
cat /etc/os-release
uname -r
printf '\nDocker CLI location\n'
command -v docker || true
printf '\nLinux Docker binaries and socket\n'
ls -l /usr/bin/docker /usr/bin/dockerd /var/run/docker.sock 2>/dev/null || true
if command -v docker >/dev/null; then
  printf '\nContexts\n'
  docker context ls
  printf '\nClient/server versions\n'
  docker version
  printf '\nCompose version\n'
  docker compose version
  printf '\nEngine identity\n'
  docker info --format 'Name={{.Name}} OS={{.OperatingSystem}} Root={{.DockerRootDir}}'
  printf '\nExisting containers\n'
  docker ps -a --format '{{.Names}} | {{.Image}} | {{.Status}}'
  printf '\nExisting volumes\n'
  docker volume ls
fi
printf '\nInventory finished; individual command errors above remain unresolved.\n'
