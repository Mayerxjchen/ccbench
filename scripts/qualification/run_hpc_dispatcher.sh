#!/usr/bin/env bash
set -euo pipefail

qualification_repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
qualification_python=${CCBENCH_PYTHON:-"$qualification_repo_root/.venv/bin/python"}

if [[ ! -x "$qualification_python" ]]; then
  echo "Python is not executable: $qualification_python" >&2
  exit 2
fi

exec "$qualification_python" \
  "$qualification_repo_root/scripts/infra/qualify_hpc_dispatcher.py" \
  --cp2k-lock "$qualification_repo_root/runtimes/locks/cp2k-runtime.lock.json" \
  "$@"
