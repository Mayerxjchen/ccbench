#!/usr/bin/env bash
set -euo pipefail
TEST_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$TEST_ROOT/verifier/check.py" ]]; then
  exec python3 "$TEST_ROOT/verifier/check.py" "${1:-/submission}"
fi
exec python3 "$TEST_ROOT/check.py" "${1:-/submission}"
