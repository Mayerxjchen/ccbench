#!/bin/bash
# Build / reuse the immutable MatClaw CIPS GPU runtime, qualify CPU/GPU parity,
# estimate the stage timeout from the probe, and launch one reference workflow
# on the GPU image by digest. The CPU image tag is never touched.
#
# Formal-host usage:
#   scripts/run_gpu_paper.sh --case 031 --profile smoke --build --probe-steps 100
#   scripts/run_gpu_paper.sh --case 032 --profile smoke --probe-steps 100
#   scripts/run_gpu_paper.sh --case 033 --profile smoke --probe-steps 100
#
# CLI:
#   run_gpu_paper.sh --case 031|032|033 --profile smoke|paper \
#     [--build] [--probe-steps INT] [--gpu-device DEV] [--seed INT] [--output DIR]
set -euo pipefail

case_id=""
profile=""
build=false
probe_steps=100
gpu_device="0"
seed=""
output=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --case) case_id="$2"; shift 2 ;;
    --profile) profile="$2"; shift 2 ;;
    --build) build=true; shift ;;
    --probe-steps) probe_steps="$2"; shift 2 ;;
    --gpu-device) gpu_device="$2"; shift 2 ;;
    --seed) seed="$2"; shift 2 ;;
    --output) output="$2"; shift 2 ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
gpu_image="dftworld-base-matclaw-cips:2.2.11-gpu"
gpu_build_dir="$repo_root/base-env-build/matclaw-cips-gpu"

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
[[ "$profile" == "smoke" || "$profile" == "paper" ]] || { echo "--profile must be smoke|paper" >&2; exit 2; }

# Build the GPU image only (never retag the CPU image; never build a CPU tag).
if [[ "$build" == true ]]; then
  echo "=== Building $gpu_image (from $gpu_build_dir/Dockerfile) ==="
  docker build \
    -f "$gpu_build_dir/Dockerfile" \
    -t "$gpu_image" \
    "$repo_root"
fi

# Immutable identity: the formal path must run on a resolved @sha256: digest.
gpu_digest="$(docker image inspect --format '{{index .RepoDigests 0}}' "$gpu_image" 2>/dev/null || true)"
if [[ -z "$gpu_digest" || "$gpu_digest" != *"@sha256:"* ]]; then
  echo "no @sha256: identity for $gpu_image; build/verify it first (--build)" >&2
  exit 2
fi
echo "gpu_image_digest=$gpu_digest"

# Output workspace (absolute; fresh run dir).
if [[ -z "$output" ]]; then
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  output="$repo_root/jobs/$ts-${case_id}-gpu-${profile}"
fi
test "${output#/}" != "$output" || { echo "--output must be absolute" >&2; exit 2; }
mkdir -p "$output"

# Derive the seed from the locked public profile when not overridden.
if [[ -z "$seed" ]]; then
  seed="$(python3 -c "
import json, sys
p = json.load(open(sys.argv[1]))['$profile']
print(p['seed'])
" "$case_dir/public/run_profiles.json")"
fi

# 1) CPU/GPU parity qualification probe on the GPU device.
echo "=== Qualifying CPU/GPU parity (steps=$probe_steps, device=$gpu_device) ==="
docker run --rm --gpus "device=$gpu_device" "$gpu_digest" \
  /opt/matclaw/bin/python /opt/matclaw/qualify_gpu.py \
  --structure /opt/matclaw/assets/CuInP2S6.cif \
  --model /opt/matclaw/assets/frozen_model.pb \
  --supercell 1 1 1 --temperature 300 --steps "$probe_steps" \
  --json-out /tmp/qualify_gpu.json \
  > "$output/qualify_gpu.json"
echo "--- qualify_gpu.json ---"
cat "$output/qualify_gpu.json"

# The probe writes only the pass records to stdout if it is the child path;
# the parent prints the full report. Fail closed unless the report says ok.
python3 - "$output/qualify_gpu.json" <<'PY'
import json
import sys
report = json.load(open(sys.argv[1]))
ok = (
    report.get("gpu_visible") is True
    and report.get("energy_abs_diff_eV", 1) < 1e-6
    and report.get("max_force_component_abs_diff_eV_A", 1) < 1e-6
    and report.get("md_finite") is True
    and report.get("frames_ok") is True
)
if not ok:
    print("GPU qualification FAILED:", json.dumps(report, indent=2), file=sys.stderr)
    sys.exit(1)
print(f"GPU qualification OK: gpu={report.get('gpu_name')} "
      f"seconds_per_step={report.get('seconds_per_step')}")
PY

# 2) Timeout estimate = ceil(1.5 * measured probe seconds); persist + print.
python3 - "$output/qualify_gpu.json" "$output" "$case_id" "$profile" "$probe_steps" "$gpu_digest" <<'PY'
import json
import math
import sys
from datetime import datetime, timezone

report_path, output, case_id, profile = sys.argv[1:5]
probe_steps, gpu_digest = int(sys.argv[5]), sys.argv[6]
report = json.load(open(report_path))
measured = float(report.get("elapsed_s", 0.0))
timeout = int(math.ceil(1.5 * measured))
estimate = {
    "case": case_id,
    "profile": profile,
    "measured_seconds": round(measured, 3),
    "probe_steps": probe_steps,
    "timeout_seconds": timeout,
    "rule": "ceil(1.5 * measured_seconds)",
    "gpu_image_digest": gpu_digest,
    "qualify_gpu_report": report_path,
    "date_utc": datetime.now(timezone.utc).isoformat(),
}
with open(f"{output}/gpu_timeout_estimate.json", "w") as handle:
    json.dump(estimate, handle, indent=2)
    handle.write("\n")
print(f"timeout estimate: measured={measured:.3f}s -> timeout_seconds={timeout}")
PY

# 3) Launch the reference workflow on the immutable GPU digest, device 0.
echo "=== Launching $case_id reference ($profile) on $gpu_digest (gpu device $gpu_device) ==="
bash "$repo_root/scripts/run_matclaw_reference.sh" \
  --case "$case_id" \
  --profile "$profile" \
  --seed "$seed" \
  --image "$gpu_digest" \
  --output "$output" \
  --evidence-class diagnostic \
  --gpus "$gpu_device"

echo "=== done: $output ==="
