#!/bin/bash
# Run one isolated MatClaw reference workflow (smoke, diagnostic, or formal).
#
# Stages only public/ into a fresh workspace, mounts the solution read-only,
# and executes the case's deterministic solve.sh inside the pinned runtime
# image. Formal runs additionally require a clean worktree, a paper profile,
# an immutable image digest, an empty output workspace, and a GPU device.
#
# CLI:
#   run_matclaw_reference.sh \
#     --case 031|032|033 \
#     --profile smoke|paper \
#     --seed INT \
#     --image IMAGE[@sha256:DIGEST] \
#     --output ABS_PATH \
#     --evidence-class diagnostic|formal \
#     [--gpus DEVICE]
set -euo pipefail

case_id=""
profile=""
seed=""
image=""
output=""
evidence_class="diagnostic"
gpu_device=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --case) case_id="$2"; shift 2 ;;
    --profile) profile="$2"; shift 2 ;;
    --seed) seed="$2"; shift 2 ;;
    --image) image="$2"; shift 2 ;;
    --output) output="$2"; shift 2 ;;
    --evidence-class) evidence_class="$2"; shift 2 ;;
    --gpus) gpu_device="$2"; shift 2 ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

case "$case_id" in
  031) case_slug="active-distillation" ;;
  032) case_slug="curie-temperature" ;;
  033) case_slug="domain-wall-search" ;;
  *)
    echo "--case must be 031|032|033" >&2
    exit 2
    ;;
esac
case_dir="$repo_root/${case_id}-matclaw-cips-${case_slug}"

test -n "$profile" || { echo "--profile is required" >&2; exit 2; }
test -n "$seed" || { echo "--seed is required" >&2; exit 2; }
test -n "$image" || { echo "--image is required" >&2; exit 2; }
test "${output#/}" != "$output" || { echo "--output must be absolute" >&2; exit 2; }

mkdir -p "$output/workspace" "$output/artifacts"

if [[ "$evidence_class" == "formal" ]]; then
  [[ "$profile" == "paper" ]] || { echo "--evidence-class formal requires --profile paper" >&2; exit 2; }
  [[ "$image" == *"@sha256:"* ]] || { echo "--evidence-class formal requires an image digest (@sha256:)" >&2; exit 2; }
  test -n "$gpu_device" || { echo "--evidence-class formal requires --gpus DEVICE" >&2; exit 2; }
  if [[ -n "$(git -C "$repo_root" status --porcelain)" ]]; then
    echo "--evidence-class formal requires a clean worktree" >&2
    exit 2
  fi
  if [[ -n "$(find "$output/workspace" -mindepth 1 -maxdepth 1 2>/dev/null)" ]]; then
    echo "--evidence-class formal requires an empty output workspace" >&2
    exit 2
  fi
fi

# Stage public inputs only after the formal freshness gate.  Checking after
# this copy would make every genuine formal run reject its own staged inputs.
cp -a "$case_dir/public/." "$output/workspace/"

docker_args=(run --rm --mount "type=bind,src=$output/workspace,dst=/app" \
  --mount "type=bind,src=$case_dir/solution,dst=/solution,readonly" \
  --env "MATCLAW_PROFILE=$profile" --env "MATCLAW_SEED=$seed" \
  --env "MATCLAW_OUTPUT=/app")
# Explicit GPU allocation whenever a device is named — including diagnostic
# runs, so a GPU run is never silently demoted to CPU by class. Formal still
# additionally requires a device to be present.
if [[ -n "$gpu_device" ]]; then
  docker_args+=(--gpus "device=$gpu_device")
fi

set +e
docker "${docker_args[@]}" "$image" bash /solution/solve.sh
exit_code=$?
set -e

# Preliminary manifest: verifier_report is filled by the hidden verifier; the
# durable v2 evidence manifest (curated artifacts + stored bundle + both CAS
# locations) is produced after the run by scripts/evidence/finalize_run.py.
# This record carries identity only — it never scans the full workspace, which
# is no longer a permanent archive.
git_commit="$(git -C "$repo_root" rev-parse HEAD)"
if [[ -n "$(git -C "$repo_root" status --porcelain)" ]]; then
  git_clean=false
else
  git_clean=true
fi

run_id="$(basename "$output")"
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
finished_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
workspace_identity="$output/workspace"

python - "$output" "$evidence_class" "$case_id" "$profile" "$seed" "$git_commit" "$git_clean" \
  "$image" "$run_id" "$started_at" "$finished_at" "$workspace_identity" "$exit_code" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

output, evidence_class, case_id, profile = sys.argv[1:5]
seed, git_commit, git_clean = sys.argv[5:8]
image, run_id = sys.argv[8:10]
started_at, finished_at, workspace_identity = sys.argv[10:13]
exit_code = int(sys.argv[13])

# No full-workspace artifact scan: the curated artifact list with roles is
# authored by finalize_run.py once the policy-resolved bundle is stored.
artifacts = []

manifest = {
    "schema_version": "1.0",
    "evidence_class": evidence_class,
    "case": case_id,
    "profile": profile,
    "seed": int(seed),
    "run_id": run_id,
    "started_at": started_at,
    "finished_at": finished_at,
    "exit_status": exit_code,
    "git_commit": git_commit,
    "git_clean": git_clean,
    "gpu_image": image,
    "gpu_image_digest": image if "@sha256:" in image else "",
    "cpu_verifier_image": "",
    "cpu_verifier_image_digest": "",
    "hardware": {},
    "software": {},
    "command": ["bash", "/solution/solve.sh"],
    "workspace_identity": workspace_identity,
    "artifacts": artifacts,
    "verifier_report": {"valid": False, "note": "preliminary; hidden verifier runs in Task 5"},
}
(Path(output) / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
PY

exit "$exit_code"
