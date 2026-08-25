#!/bin/bash
# Finalize one completed 032 formal re-run as a recoverable v2 transaction.
#
# ER6/ER8: the durable evidence for each paper job is a deterministic bundle,
# stored in primary + replica CAS and restored, NOT a permanent local workspace.
# The cluster run workspace is only temporary staging for curation:
#   1. rsync the cluster run workspace -> a local staging dir (gitignored)
#   2. finalize_run.py freezes -> curates to the policy-resolved set -> runs the
#      hidden verifier over original/curated/restored in the cpu SIF on the
#      cluster -> bundles -> stores twice with read-back verify -> restores and
#      re-verifies -> commits the v2 manifest. The state machine persists
#      finalize-state.json, so re-running resumes instead of overwriting.
#   3. delete the local staging workspace once the transaction is committed
#
# Usage (repo root, host has ssh <site-alias>):
#   scripts/reference/finalize_032_evidence.sh [--only run-1|run-2]
#
# Safe by design: a sealed (MANIFEST_COMMITTED) run refuses to re-run; the only
# mutable effect before sealing is the temporary local staging workspace.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$ROOT/.venv/bin/python"
HOST=<site-alias>
BASE=/public/home/<site-user>/dftworld2-runs/matclaw-032
CPU_SIF=/public/home/<site-user>/dftworld2-runs/matclaw-031/runtime/matclaw-cips-2.2.11-cpu-amd64.sif
TESTS_SRC="$ROOT/032-matclaw-cips-curie-temperature/tests"
EVIDENCE="$ROOT/evidence/matclaw/formal/032"
CASE_DIR="$ROOT/032-matclaw-cips-curie-temperature"
POLICY="$CASE_DIR/reference/evidence-policy.json"
GIT_COMMIT=71078a4

# Host-local CAS: primary + independent replica (gitignored, never committed).
PRIMARY="cas+file://$ROOT/evidence/matclaw/store/032-primary"
REPLICA="cas+file://$ROOT/evidence/matclaw/store/032-replica"

GPU_IMAGE=dftworld-base-matclaw-cips:2.2.11-gpu-amd64
GPU_DIGEST=sha256:34db42302a16a3b4b83276597bec1e0a0776cc906863cb3f0674d6e0d03919b0
CPU_IMAGE=dftworld-base-matclaw-cips:2.2.11-cpu-amd64
CPU_DIGEST=sha256:f36968b0e3422f13fde3664dd5e5f1ae0c10143c50a65476feae40ec354a05e9
LOCKED_SIF_SHA=99aefeff8f457cd6b4f57e1592db511f167ab493730f970bfd7baa68993250c3
APPTAINER=/public/software/apptainer/bin/apptainer

# seed -> run-id mapping (frozen formal identity)
declare -A SEED_TO_RUN=( [2026081206]=run-1 [2026081213]=run-2 )

only="${1:-}"
case "$only" in
  ""|--only) ;;
  --only)
    shift
    only="$1"
    [ -n "$only" ] || { echo "need run id after --only"; exit 1; }
    ;;
  *) echo "usage: $0 [--only run-1|run-2]"; exit 1 ;;
esac

run_one() {
  local seed="$1" run_id="$2"
  local cluster_ws="$BASE/runs/formal-${seed}/workspace"
  local ev_dir="$EVIDENCE/$run_id"
  local ev_ws="$ev_dir/workspace"

  echo "== finalize $run_id (seed $seed)"

  # Sealed runs are immutable; refuse rather than resurrect.
  if [ -f "$ev_dir/finalize-state.json" ] && \
     grep -q '"MANIFEST_COMMITTED"' "$ev_dir/finalize-state.json"; then
    echo "   already MANIFEST_COMMITTED — skipping $run_id" >&2
    return 0
  fi

  # job + hardware identity from sacct (must have completed)
  local job_id started_at finished_at node exit_code
  local sacct_line
  sacct_line=$(ssh -o BatchMode=yes "$HOST" \
    "sacct -X -j matclaw-032-paper-${seed} --format=JobID,State,Start,End,NodeList,ExitCode -n 2>/dev/null | head -1")
  job_id=$(echo "$sacct_line" | awk '{print $1}')
  started_at=$(echo "$sacct_line" | awk '{print $3}')
  finished_at=$(echo "$sacct_line" | awk '{print $4}')
  node=$(echo "$sacct_line" | awk '{print $5}')
  exit_code=$(echo "$sacct_line" | awk '{print $6}')
  if [ -z "$job_id" ] || [ -z "$started_at" ] || [ -z "$finished_at" ]; then
    echo "job for seed $seed not completed yet: $(echo "$sacct_line" | awk '{print $2}')" >&2
    return 1
  fi
  if [ "$exit_code" != "0:0" ]; then
    echo "job $job_id exit $exit_code (not 0:0) — refusing to finalize" >&2
    return 1
  fi
  # Slurm UTC -> ISO Z
  local s_iso f_iso
  s_iso=$(echo "$started_at" | tr 'T' ' ' | sed 's/ /T/; s/$/Z/')
  f_iso=$(echo "$finished_at" | tr 'T' ' ' | sed 's/ /T/; s/$/Z/')
  echo "   job=$job_id node=$node started=$s_iso finished=$f_iso exit=$exit_code"

  # 1. cluster workspace -> local staging (temporary curation input)
  mkdir -p "$ev_ws"
  rsync -a "<site-alias>:$cluster_ws/." "$ev_ws/"

  # 2. finalize as a recoverable v2 transaction
  "$PY" "$ROOT/scripts/evidence/finalize_run.py" \
    --run-dir "$ev_dir" \
    --workspace "$ev_ws" \
    --case-dir "$CASE_DIR" \
    --policy "$POLICY" \
    --primary "$PRIMARY" \
    --replica "$REPLICA" \
    --verifier-host "$HOST" \
    --verifier-sif "$CPU_SIF" \
    --verifier-tests "$TESTS_SRC" \
    --verifier-apptainer "$APPTAINER" \
    --verifier-scratch "$BASE" \
    --seed "$seed" --run-id "$run_id" \
    --git-commit "$GIT_COMMIT" --git-clean true \
    --gpu-image "$GPU_IMAGE" --gpu-image-digest "$GPU_DIGEST" \
    --cpu-verifier-image "$CPU_IMAGE" --cpu-verifier-image-digest "$CPU_DIGEST" \
    --workspace-identity "032-${seed}" \
    --started-at "$s_iso" --finished-at "$f_iso" \
    --hardware-json "{\"job\":\"$job_id\",\"node\":\"$node\",\"partition\":\"gpu\",\"gres\":\"gpu:1\"}" \
    --software-json "{\"apptainer_sif_sha256\":\"$LOCKED_SIF_SHA\"}" \
    --profile paper

  # 3. committed: staging workspace is a cache, not an archive — drop it.
  rm -rf "$ev_ws"
  echo "   sealed bundle for $run_id (state: $ev_dir/finalize-state.json)"
}

for seed in "${!SEED_TO_RUN[@]}"; do
  run_id="${SEED_TO_RUN[$seed]}"
  if [ -n "$only" ] && [ "$run_id" != "$only" ]; then continue; fi
  run_one "$seed" "$run_id"
done
