#!/usr/bin/env bash
set -euo pipefail

qualification_repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
qualification_max_attempts=${QUALIFY_MAX_ATTEMPTS:-6}
qualification_retry_seconds=${QUALIFY_RETRY_SECONDS:-600}
qualification_attempt=1

while (( qualification_attempt <= qualification_max_attempts )); do
  if "$qualification_repo_root/scripts/qualification/run_hpc_dispatcher.sh" "$@"; then
    exit 0
  fi
  if (( qualification_attempt == qualification_max_attempts )); then
    break
  fi
  echo "qualification attempt $qualification_attempt failed; retrying in $qualification_retry_seconds seconds" >&2
  sleep "$qualification_retry_seconds"
  qualification_attempt=$((qualification_attempt + 1))
done

echo "qualification failed after $qualification_max_attempts attempts" >&2
exit 1
