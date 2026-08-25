#!/bin/bash
# ER8 cluster-native finalize for the 033 formal re-runs (seeds 2026081301/2026081305).
#
# Mirrors finalize_032_evidence_cluster.sh: the workspaces are built on the
# cluster by the re-run jobs (submit_033_formal.sh stages public/ into
# runs/formal-<seed>/workspace), so nothing crosses the host<->cluster link but
# the small pipeline payload and the manifests.
#
#   1. rsync the small pipeline payload (scripts/, schemas/, 033 reference/
#      tests/, acceptance.json) up to $BASE/payload.
#   2. On the login node, per run: finalize_run.py --verifier-slurm with the
#      cluster workspace as input, cluster CAS store dirs as primary/replica.
#      The transaction curates, verifies original==curated==restored with the
#      frozen CPU SIF (sbatch jobs on a compute node), bundles, stores twice,
#      restores, and commits the v2 manifest.
#   3. rsync back only manifest.json + finalize-state.json + the small curated/
#      bundle descriptors. No workspace bytes return.
#
# State machine is resumable: re-running skips sealed runs.
#
# Usage (repo root):
#   scripts/reference/finalize_033_evidence_cluster.sh [--only run-1|run-2]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOST=<site-alias>
BASE=/public/home/<site-user>/dftworld2-runs/matclaw-033
PAYLOAD="$BASE/payload"
CPU_SIF=/public/home/<site-user>/dftworld2-runs/matclaw-031/runtime/matclaw-cips-2.2.11-cpu-amd64.sif
APPTAINER=/public/software/apptainer/bin/apptainer
# 033 formal evidence pins the frozen pre-HPC-contract commit 1b61084 — same
# code the original v1 formal runs used (run_search.py 7fbd17f7).
GIT_COMMIT=1b61084

# cluster-side evidence + store layout
CL_EV="$BASE/evidence/033"          # run-1/ run-2/ on the cluster
CL_STORE_PRIMARY="$BASE/store/033-primary"
CL_STORE_REPLICA="$BASE/store/033-replica"

# host-side evidence dir (only manifests come back)
EVIDENCE="$ROOT/evidence/matclaw/formal/033"
CASE_DIR="$ROOT/033-matclaw-cips-domain-wall-search"
CL_CASE="033-matclaw-cips-domain-wall-search"   # cluster-relative under $PAYLOAD
TESTS_SRC="$CASE_DIR/tests"

GPU_IMAGE=/runtime/matclaw-cips-2.2.11-gpu-amd64.sif
GPU_DIGEST=sha256:99aefeff8f457cd6b4f57e1592db511f167ab493730f970bfd7baa68993250c3
CPU_IMAGE=/runtime/matclaw-cips-2.2.11-cpu-amd64.sif
CPU_DIGEST=sha256:f104256e5b41f9cdc0304fe5d5ab4184c57b507daba564cfd3328f8c60b96d29
LOCKED_SIF_SHA=99aefeff8f457cd6b4f57e1592db511f167ab493730f970bfd7baa68993250c3

# bash 3.2 (macOS) has no associative arrays — parallel indexed arrays instead
SEEDS=(2026081301 2026081305)
RUN_IDS=(run-1 run-2)

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

# ---- 2. finalize each run on the login node ---------------------------------
run_one() {
  local seed="$1" run_id="$2"
  local cluster_ws="$BASE/runs/formal-${seed}/workspace"
  local ev_dir="$CL_EV/$run_id"

  # sealed runs are immutable
  if ssh -o BatchMode=yes "$HOST" "test -f '$ev_dir/finalize-state.json' && grep -q 'MANIFEST_COMMITTED' '$ev_dir/finalize-state.json'"; then
    echo "   already MANIFEST_COMMITTED — skipping $run_id" >&2
    return 0
  fi

  # paper-job identity from sacct (must have completed)
  local sacct_line
  sacct_line=$(ssh -o BatchMode=yes "$HOST" \
    "sacct -X --name=matclaw-033-paper-${seed} --format=JobID,State,Start,End,NodeList,ExitCode -n 2>/dev/null | head -1")
  local job_id started_at finished_at node exit_code
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
  local s_iso f_iso
  s_iso=$(echo "$started_at" | tr 'T' ' ' | sed 's/ /T/; s/$/Z/')
  f_iso=$(echo "$finished_at" | tr 'T' ' ' | sed 's/ /T/; s/$/Z/')
  echo "   job=$job_id node=$node started=$s_iso finished=$f_iso exit=$exit_code"

  # Run the transaction detached on the login node (verifier sbatch jobs can take
  # a while; the ssh control connection must not own the transaction lifetime).
  # nohup + state-file polling: sealed runs are immutable, so an interrupted
  # transaction resumes from its last durable state on re-run.
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
      --workspace-identity '033-${seed}' \
      --started-at '$s_iso' --finished-at '$f_iso' \
      --hardware-json '{\"job\":\"$job_id\",\"node\":\"$node\",\"partition\":\"gpu\",\"gres\":\"gpu:1\"}' \
      --software-json '{\"apptainer_sif_sha256\":\"$LOCKED_SIF_SHA\"}' \
      --profile paper < /dev/null > '$ev_dir/finalize.log' 2>&1 &
    echo started"

  # poll the durable state file until the transaction seals (or fails)
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

  # ---- 3. bring back only the small records --------------------------------
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
  run_one "$seed" "$run_id"
done

echo "== cluster-native finalize complete"
echo "   store primary:  $CL_STORE_PRIMARY"
echo "   store replica:  $CL_STORE_REPLICA"
echo "   host manifests: $EVIDENCE"
