#!/usr/bin/env bash
set -euo pipefail

# Compatibility entrypoint; monitor owns parsing, logging, and observation.
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec bash "$script_dir/mail-monitor.sh" "$@"
