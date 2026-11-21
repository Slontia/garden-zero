#!/usr/bin/env bash
set -euo pipefail
core=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
exec bash "$core/scripts/host-command.sh" start "${1:-all}"
