#!/bin/bash
# Frozen Case 031 formal-evidence harness. Render __RUN_DIR__, __SEED__,
# __RUN_ID__, and __GIT_COMMIT__ before submission. The solver cannot see the
# hidden verifier: /tests is mounted only in the second Apptainer invocation.
#SBATCH --job-name=031-formal-__RUN_ID__
#SBATCH --account=acct-blocked
#SBATCH --partition=gpu
#SBATCH --qos=normal
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=slurm-%j.out
set -euo pipefail

RUN=__RUN_DIR__
SEED=__SEED__
RUN_ID=__RUN_ID__
GIT_COMMIT=__GIT_COMMIT__
GPU_SIF=/public/home/<site-user>/dftworld2-runs/matclaw-031/runtime/matclaw-cips-2.2.11-gpu-amd64.sif
GPU_SHA=99aefeff8f457cd6b4f57e1592db511f167ab493730f970bfd7baa68993250c3
CPU_SIF=/public/home/<site-user>/dftworld2-runs/matclaw-031/runtime/matclaw-cips-2.2.11-cpu-amd64.sif
CPU_SHA=f104256e5b41f9cdc0304fe5d5ab4184c57b507daba564cfd3328f8c60b96d29
APPTAINER=/public/software/apptainer/bin/apptainer
WORKSPACE=$RUN/workspace
SOLUTION=$RUN/private/solution
TESTS=$RUN/private/tests

fail_infra() {
  printf 'infrastructure_failed: %s\n' "$*" | tee "$RUN/INFRA_FAILED.txt"
  exit 90
}

test "$(uname -m)" = x86_64 || fail_infra "node architecture mismatch"
test "$(shasum -a 256 "$GPU_SIF" | cut -d' ' -f1)" = "$GPU_SHA" || fail_infra "GPU SIF SHA mismatch"
test "$(shasum -a 256 "$CPU_SIF" | cut -d' ' -f1)" = "$CPU_SHA" || fail_infra "CPU verifier SIF SHA mismatch"
test -x "$SOLUTION/solve.sh" || fail_infra "frozen solution missing"
test -f "$TESTS/verifier.py" || fail_infra "hidden verifier missing"
test -f "$WORKSPACE/run_profiles.json" || fail_infra "public profile missing"
test ! -e "$WORKSPACE/checkpoint.json" || fail_infra "workspace is not fresh"
GPU_LINE=$(nvidia-smi -L 2>/dev/null | head -1) || fail_infra "GPU unavailable"
STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf 'infra_gates=pass node=%s gpu=%s commit=%s seed=%s\n' \
  "$(hostname)" "$GPU_LINE" "$GIT_COMMIT" "$SEED" | tee "$RUN/INFRA_PASS.txt"

# Scientific execution. Hidden tests are deliberately not mounted here.
"$APPTAINER" exec --nv --containall \
  --env MATCLAW_PROFILE=paper --env MATCLAW_SEED="$SEED" \
  --env MATCLAW_OUTPUT=/app --bind "$WORKSPACE:/app" \
  --bind "$SOLUTION:/solution:ro" "$GPU_SIF" /solution/solve.sh

# Independent hidden recomputation in the separately locked CPU runtime.
mkdir -p "$RUN/artifacts"
set +e
"$APPTAINER" exec --containall \
  --bind "$WORKSPACE:/app:ro" --bind "$TESTS:/tests:ro" \
  "$CPU_SIF" /opt/matclaw/bin/python -c \
  'import json,sys; sys.path.insert(0,"/tests"); from verifier import verify; r=verify(__import__("pathlib").Path("/app"),"paper"); print(json.dumps(r,indent=2)); raise SystemExit(0 if r["valid"] else 1)' \
  > "$RUN/artifacts/verifier_report.json"
VERIFY_EXIT=$?
set -e
if test "$VERIFY_EXIT" -ne 0; then
  printf 'hidden_verifier_failed exit=%s\n' "$VERIFY_EXIT" >&2
  cat "$RUN/artifacts/verifier_report.json" >&2
  exit "$VERIFY_EXIT"
fi

FINISHED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
NODE=$(hostname)
python3 - "$RUN" "$RUN_ID" "$SEED" "$GIT_COMMIT" "$STARTED_AT" "$FINISHED_AT" "$NODE" "$GPU_LINE" "$GPU_SHA" "$CPU_SHA" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
run_id, seed, commit = sys.argv[2], int(sys.argv[3]), sys.argv[4]
started, finished, node, gpu = sys.argv[5:9]
gpu_sha, cpu_sha = sys.argv[9:11]
report_path = run / "artifacts/verifier_report.json"
report = json.loads(report_path.read_text())
if report.get("valid") is not True:
    raise SystemExit("refusing to manifest an invalid hidden-verifier report")

artifacts = []
for root in (run / "workspace", run / "artifacts", run / "private" / "solution", run / "private" / "tests"):
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            artifacts.append({"path": str(path.relative_to(run)), "sha256": digest})

manifest = {
    "schema_version": "1.0",
    "evidence_class": "formal",
    "case": "031",
    "profile": "paper",
    "seed": seed,
    "run_id": run_id,
    "started_at": started,
    "finished_at": finished,
    "exit_status": 0,
    "git_commit": commit,
    "git_clean": True,
    "gpu_image": "/runtime/matclaw-cips-2.2.11-gpu-amd64.sif",
    "gpu_image_digest": "sha256:" + gpu_sha,
    "cpu_verifier_image": "/runtime/matclaw-cips-2.2.11-cpu-amd64.sif",
    "cpu_verifier_image_digest": "sha256:" + cpu_sha,
    "hardware": {"node": node, "gpu": gpu, "partition": "gpu", "gres": "gpu:1"},
    "software": {"python": "3.11.15", "tensorflow": "2.16.2", "deepmd": "2.2.11", "ase": "3.26.0"},
    "command": ["/solution/solve.sh"],
    "workspace_identity": str((run / "workspace").resolve()),
    "artifacts": artifacts,
    "verifier_report": {
        "valid": True,
        "errors": report.get("errors", []),
        "metrics": {
            "final_force_mae_eV_A": report["recomputed_final_mae_eV_A"],
            "active_iterations": report["active_iterations"],
            "source_relative_error": report["source_relative_error"],
            "heldout_mae_history_eV_A": report["recomputed_heldout_mae_eV_A"],
        },
        "report_artifact": "artifacts/verifier_report.json",
    },
}
(run / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps(manifest["verifier_report"], indent=2))
PY
