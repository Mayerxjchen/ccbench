#!/bin/bash
# ER8 cluster-native finalize for the 031 formal re-runs (seeds 2026081201/2026081202).
#
# Unlike 032, the cluster has NO surviving 031 workspace bytes (only the runtime
# SIF); the v1 evidence was brought to the host (evidence/matclaw/formal/031).
# So this driver first rsyncs the host workspaces back to their ORIGINAL cluster
# paths ($BASE/runs/formal-<seed>/workspace), then runs the same login-node
# transaction + frozen-CPU-SIF verifier via sbatch as 032, storing twice in the
# cluster CAS and bringing only the small manifests back.
#
# Measured link: ~2.9 MB/s (10 MB in 3.5 s), so the unique ~564 MB upload is a
# few minutes, not hours. Hardlinks (-H) are preserved so the two runs share
# committee-model inodes and only unique bytes cross.
#
# Usage (repo root):
#   scripts/reference/finalize_031_evidence_cluster.sh [--only run-1|run-2]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOST=<site-alias>
BASE=/public/home/<site-user>/dftworld2-runs/matclaw-031
PAYLOAD="$BASE/payload"
CPU_SIF=/public/home/<site-user>/dftworld2-runs/matclaw-031/runtime/matclaw-cips-2.2.11-cpu-amd64.sif
APPTAINER=/public/software/apptainer/bin/apptainer
GIT_COMMIT=4da3d31

# cluster-side evidence + store layout
CL_EV="$BASE/evidence/031"          # run-1/ run-2/ on the cluster
CL_STORE_PRIMARY="$BASE/store/031-primary"
CL_STORE_REPLICA="$BASE/store/031-replica"

# host-side evidence dir (v1 bytes + where v2 manifests come back)
EVIDENCE="$ROOT/evidence/matclaw/formal/031"
CASE_DIR="$ROOT/031-matclaw-cips-active-distillation"
CL_CASE="031-matclaw-cips-active-distillation"   # cluster-relative under $PAYLOAD

# v1 identity (jobs completed 2026-08-13; local = UTC+8)
declare -a SEEDS=(2026081201 2026081202)
declare -a RUN_IDS=(run-1 run-2)
declare -a JOBS=(3545487 3545488)
declare -a NODES=(<site-node-gpu3> <site-node-gpu3>)
declare -a STARTED=("2026-08-13T09:15:08Z" "2026-08-13T09:30:12Z")
declare -a FINISHED=("2026-08-13T09:29:51Z" "2026-08-13T09:44:50Z")

GPU_IMAGE=/runtime/matclaw-cips-2.2.11-gpu-amd64.sif
GPU_DIGEST=sha256:99aefeff8f457cd6b4f57e1592db511f167ab493730f970bfd7baa68993250c3
CPU_IMAGE=/runtime/matclaw-cips-2.2.11-cpu-amd64.sif
CPU_DIGEST=sha256:f104256e5b41f9cdc0304fe5d5ab4184c57b507daba564cfd3328f8c60b96d29
HW_JSON='{"node":"<site-node-gpu3>","gpu":"GPU 0: NVIDIA A100-SXM4-80GB (UUID: GPU-1adbd92d-b266-3252-1848-f10fb939e08d)","partition":"gpu","gres":"gpu:1"}'
SW_JSON='{"python":"3.11.15","tensorflow":"2.16.2","deepmd":"2.2.11","ase":"3.26.0"}'

only="${1:-}"
case "$only" in
  ""|--only) ;;
  --only)
    shift; only="$1"
    [ -n "$only" ] || { echo "need run id after --only"; exit 1; }
    ;;
  *) echo "usage: $0 [--only run-1|run-2]"; exit 1 ;;
esac

# ---- 1. push the small pipeline payload up ----------------------------------
echo "== rsync payload to $PAYLOAD"
ssh -o BatchMode=yes "$HOST" "mkdir -p '$PAYLOAD/scripts/evidence' '$PAYLOAD/scripts/reference' '$PAYLOAD/scripts/ablation' '$PAYLOAD/schemas' '$PAYLOAD/benchmark/sources/matclaw' '$PAYLOAD/$CL_CASE/reference' '$PAYLOAD/$CL_CASE/tests'"
rsync -a "$ROOT/scripts/"                          "$HOST:$PAYLOAD/scripts/"
rsync -a "$ROOT/schemas/"                          "$HOST:$PAYLOAD/schemas/"
rsync -a "$ROOT/benchmark/sources/matclaw/acceptance.json" "$HOST:$PAYLOAD/benchmark/sources/matclaw/acceptance.json"
rsync -a "$CASE_DIR/evaluator-manifest.json"       "$HOST:$PAYLOAD/$CL_CASE/"
rsync -a "$CASE_DIR/reference/"                    "$HOST:$PAYLOAD/$CL_CASE/reference/"
rsync -a "$CASE_DIR/tests/"                        "$HOST:$PAYLOAD/$CL_CASE/tests/"
rsync -a "$ROOT/conftest.py"                      "$HOST:$PAYLOAD/conftest.py"

# ---- 2. upload host v1 workspaces to their original cluster paths -----------
for i in "${!SEEDS[@]}"; do
  run_id="${RUN_IDS[$i]}"; seed="${SEEDS[$i]}"
  if [ -n "$only" ] && [ "$run_id" != "$only" ]; then continue; fi
  ws="$BASE/runs/formal-${seed}/workspace"
  echo "== rsync host $run_id/workspace -> $ws (hardlinks preserved)"
  ssh -o BatchMode=yes "$HOST" "mkdir -p '$ws'"
  rsync -aH "$EVIDENCE/$run_id/workspace/" "$HOST:$ws/"
done

# ---- 3. finalize each run on the login node ---------------------------------
run_one() {
  local i="$1" seed="$2" run_id="$3"
  local cluster_ws="$BASE/runs/formal-${seed}/workspace"
  local ev_dir="$CL_EV/$run_id"

  # sealed runs are immutable
  if ssh -o BatchMode=yes "$HOST" "test -f '$ev_dir/finalize-state.json' && grep -q 'MANIFEST_COMMITTED' '$ev_dir/finalize-state.json'"; then
    echo "   already MANIFEST_COMMITTED — skipping $run_id" >&2
    return 0
  fi

  local job_id="${JOBS[$i]}" node="${NODES[$i]}"
  local s_iso="${STARTED[$i]}" f_iso="${FINISHED[$i]}"
  echo "   job=$job_id node=$node started=$s_iso finished=$f_iso"

  ssh -o BatchMode=yes "$HOST" \
    "mkdir -p '$ev_dir' && cd '$PAYLOAD' && PYTHONPATH='$PAYLOAD' setsid nohup python3 scripts/evidence/finalize_run.py \
      --run-dir '$ev_dir' \
      --workspace '$cluster_ws' \
      --case-dir '$PAYLOAD/$CL_CASE' \
      --policy '$PAYLOAD/$CL_CASE/reference/evidence-policy.json' \
      --primary 'cas+file://$CL_STORE_PRIMARY' \
      --replica 'cas+file://$CL_STORE_REPLICA' \
      --verifier-slurm \
      --verifier-sif '$CPU_SIF' \
      --verifier-tests '$PAYLOAD/$CL_CASE/tests' \
      --verifier-apptainer '$APPTAINER' \
      --verifier-scratch '$BASE/verify-scratch' \
      --verifier-partition cpu \
      --verifier-gres '' \
      --verifier-account acct-blocked \
      --verifier-cpus 8 --verifier-mem 64G --verifier-time 04:00:00 \
      --seed '$seed' --run-id '$run_id' \
      --git-commit '$GIT_COMMIT' --git-clean true \
      --gpu-image '$GPU_IMAGE' --gpu-image-digest '$GPU_DIGEST' \
      --cpu-verifier-image '$CPU_IMAGE' --cpu-verifier-image-digest '$CPU_DIGEST' \
      --workspace-identity '$cluster_ws' \
      --started-at '$s_iso' --finished-at '$f_iso' \
      --hardware-json '$HW_JSON' \
      --software-json '$SW_JSON' \
      --profile paper < /dev/null > '$ev_dir/finalize.log' 2>&1 &
    echo started"

  echo "   waiting for $run_id to reach MANIFEST_COMMITTED..."
  local waited=0
  while [ "$waited" -lt 3600 ]; do
    if ssh -o BatchMode=yes "$HOST" "test -f '$ev_dir/finalize-state.json' && grep -q MANIFEST_COMMITTED '$ev_dir/finalize-state.json'"; then
      echo "   $run_id sealed"
      break
    fi
    if ssh -o BatchMode=yes "$HOST" "test -s '$ev_dir/finalize.log' && grep -qE 'Traceback|FinalizeError|Error' '$ev_dir/finalize.log'"; then
      echo "   $run_id transaction FAILED:" >&2
      ssh -o BatchMode=yes "$HOST" "tail -30 '$ev_dir/finalize.log'" >&2
      return 1
    fi
    sleep 30
    waited=$((waited + 30))
  done
  if ! ssh -o BatchMode=yes "$HOST" "test -f '$ev_dir/finalize-state.json' && grep -q MANIFEST_COMMITTED '$ev_dir/finalize-state.json'"; then
    echo "   $run_id did not seal within ${waited}s (state: $(ssh -o BatchMode=yes "$HOST" "cat '$ev_dir/finalize-state.json' 2>/dev/null | head -c 300"))" >&2
    ssh -o BatchMode=yes "$HOST" "tail -40 '$ev_dir/finalize.log' 2>/dev/null" >&2
    return 1
  fi

  # ---- 4. bring back only the small records --------------------------------
  mkdir -p "$EVIDENCE/$run_id"
  rsync -a "$HOST:$ev_dir/manifest.json"        "$EVIDENCE/$run_id/manifest.json"
  rsync -a "$HOST:$ev_dir/finalize-state.json"  "$EVIDENCE/$run_id/finalize-state.json"
  rsync -a "$HOST:$ev_dir/curated-files.json"   "$EVIDENCE/$run_id/curated-files.json" 2>/dev/null || true
  rsync -a "$HOST:$ev_dir/bundle-descriptor.json" "$EVIDENCE/$run_id/bundle-descriptor.json" 2>/dev/null || true
  rsync -a "$HOST:$ev_dir/curated-report.json"  "$EVIDENCE/$run_id/curated-report.json" 2>/dev/null || true
  echo "   committed $run_id (manifest back, bundle stays in cluster CAS)"
}

for i in "${!SEEDS[@]}"; do
  seed="${SEEDS[$i]}"
  run_id="${RUN_IDS[$i]}"
  if [ -n "$only" ] && [ "$run_id" != "$only" ]; then continue; fi
  run_one "$i" "$seed" "$run_id"
done

echo "== cluster-native 031 finalize complete"
echo "   store primary:  $CL_STORE_PRIMARY"
echo "   store replica:  $CL_STORE_REPLICA"
echo "   host manifests: $EVIDENCE"
