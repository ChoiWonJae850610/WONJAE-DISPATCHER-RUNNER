#!/usr/bin/env bash
# Optional monitor daemon installed by the Owner under the same Ubuntu account as
# the home-linux GitHub Actions runner. Does not start a development job.
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
exec python3 "$repo_dir/scripts/local_monitor.py" watch "$@"
