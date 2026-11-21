#!/usr/bin/env bash
set -euo pipefail
core=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
export PYTHONDONTWRITEBYTECODE=1
if [[ $EUID != 0 ]]; then exec sudo -n /usr/bin/bash "$core/scripts/host-command.sh" "$@"; fi
pid=$(pidof dockerd)
[[ $pid =~ ^[0-9]+$ ]] || { echo 'Local Docker daemon unavailable'; exit 1; }
exec nsenter -t "$pid" -m -- /usr/bin/python3 -B "$core/scripts/nasctl.py" "$@"
