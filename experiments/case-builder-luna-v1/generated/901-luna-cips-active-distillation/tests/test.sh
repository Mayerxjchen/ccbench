#!/usr/bin/env bash
set -eu
root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
python3 "$root/tools/verify_submission.py" --dry-run
python3 -m unittest discover -s "$root/tests" -p 'test_*.py' -v
