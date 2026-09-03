#!/usr/bin/env bash
set -eu
case_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
python3 "$case_dir/../../../../scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/scripts/common/audit_candidate_bundle.py" "$case_dir" --json
