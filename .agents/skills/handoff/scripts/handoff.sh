#!/usr/bin/env bash
set -euo pipefail

# Keep the shared Bash entrypoint; Python supplies canonical paths, metadata,
# atomic no-replace publication, and process-lifetime locks on macOS/Linux.
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec python3 "$script_dir/handoff.py" "$@"
