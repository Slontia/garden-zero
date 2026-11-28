#!/usr/bin/env bash
# Manual disk backup; configured target identity and transaction checks remain enforced.
set -euo pipefail
case "${1:-}" in
  -h|--help)
    echo 'Usage: bash core/scripts/disk-backup.sh [--purge-history]'
    echo 'Default: retain history. --purge-history cleans old versions only after a successful commit.'
    exit 0 ;;
  ''|--purge-history) ;;
  *) echo 'Unknown option; use --help.' >&2; exit 2 ;;
esac
if [[ $# -gt 1 ]]; then echo 'Only --purge-history is supported.' >&2; exit 2; fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
exec bash "$script_dir/host-command.sh" disk-backup "$@"
