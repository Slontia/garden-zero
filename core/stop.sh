#!/usr/bin/env bash
set -euo pipefail
core=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
module=${1:-business}
if [[ "$module" == all ]]; then exec bash "$core/scripts/host-command.sh" shutdown; fi
exec bash "$core/scripts/host-command.sh" stop "$module"
