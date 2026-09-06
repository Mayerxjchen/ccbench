#!/bin/bash
# Case 032 restartable reference runner.
#
# The paper protocol is 14 long DP-MD trajectories (pilot 350 K + 13 production
# temperatures, ~560k steps total), which does not fit in a single
# command-window/wall-time budget.  This runner drives the SAME solver entry
# point (`/solution/solve.sh` -> `run_curie.py`) repeatedly until
# `checkpoint.json` reports `stage == "result"`.
#
# Resume semantics live in `run_curie.py` (not here): `load_checkpoint` reuses
# ONLY completed trajectory pairs (final `.traj` + `.json` sidecar) whose
# identity and sha256 still match, and a torn `*.partial.traj` is never reused
# as a completed record — it is discarded and that one temperature is re-run
# from the locked initial structure + seed.  So each invocation makes net
# forward progress: completed temperatures are skipped, the first incomplete
# temperature is re-run from step 0, and the rest follow.  No `.partial.traj`
# survives once the run reaches `stage == "result"` (the atomic rename to the
# final name + sidecar promotes each trajectory on completion).
#
# Usage (host-side persistent container, e.g. the Mac test-bed):
#   scripts/run_matclaw_reference_restartable.sh CONTAINER_ID
#
# The container must have `/solution` (read-only) and the run workspace bind at
# `/app`, and `MATCLAW_PROFILE` / `MATCLAW_SEED` / `MATCLAW_OUTPUT` must already
# be exported INTO the container (apptainer `--env` flags, or `docker exec -e`
# for a plain container).  This script only re-invokes; it owns no state.
#
# On HPC the equivalent is a controller resubmit loop: submit -> wait_for ->
# fetch -> read `checkpoint.json["stage"]`; resubmit the same run_id until it
# is `"result"`.  This script is the test-bed/persistent-container form of
# that loop.  DIAGNOSTIC / reference-internal: not part of the graded agent
# interface.

set -euo pipefail

CONTAINER="${1:?usage: $0 CONTAINER_ID}"
OUTPUT_DIR="${MATCLAW_OUTPUT:-/app}"
# How many invocations before we give up and report incomplete (the caller can
# re-run this script itself to keep going).  0 = unbounded.
MAX_INVOCATIONS="${MAX_INVOCATIONS:-0}"

env_flags=()
[ -n "${MATCLAW_PROFILE:-}" ] && env_flags+=( -e "MATCLAW_PROFILE=${MATCLAW_PROFILE}" )
[ -n "${MATCLAW_SEED:-}"    && env_flags+=( -e "MATCLAW_SEED=${MATCLAW_SEED}" )
[ -n "${MATCLAW_OUTPUT:-}"  && env_flags+=( -e "MATCLAW_OUTPUT=${MATCLAW_OUTPUT}" )

n=0
while true; do
  n=$((n + 1))
  echo "==> invocation #$n: /solution/solve.sh"
  # A killed exec mid-trajectory is expected: solve.sh exits non-zero on SIGTERM
  # or when its own time budget is hit.  We do NOT treat that as failure — the
  # checkpoint has recorded every completed temperature up to the kill point.
  docker exec "${env_flags[@]}" "$CONTAINER" /solution/solve.sh || true

  # stage == "result" => the run is complete and result.json is written.
  if docker exec "$CONTAINER" /opt/matclaw/bin/python -c \
      "import json,sys; d=json.load(open('${OUTPUT_DIR}/checkpoint.json')); sys.exit(0 if d.get('stage')=='result' else 1)"; then
    echo "==> checkpoint.json stage=result; run complete after $n invocation(s)."
    exit 0
  fi

  if [ "$MAX_INVOCATIONS" -gt 0 ] && [ "$n" -ge "$MAX_INVOCATIONS" ]; then
    echo "==> reached MAX_INVOCATIONS=$MAX_INVOCATIONS without stage=result; "
    echo "    re-run this script to continue (completed temperatures are reused)."
    exit 1
  fi
done
