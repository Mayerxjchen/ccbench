#!/bin/bash
# ============================================================================
# provenance.sh — record the exact runtime manifest for a reference calibration
# (Task 5).  Called on the compute node AFTER solution/expert/run.sh completes,
# inside the same container, with the workflow env (env.sh) available.
#
# Usage: bash provenance.sh <manifest.json>
#
# Records the root seed, reference run id, schedule profile, package versions,
# binary SHA-256 hashes, SLURM job id, and compute host.  This is what makes the
# two reference runs auditable: identical manifest fields + distinct declared
# seeds = a true two-realization anchor.
# ============================================================================
set -euo pipefail
source "$(dirname "$0")/env.sh"

OUT="${1:?manifest path}"
mkdir -p "$(dirname "$OUT")"

python3 - "$OUT" <<'PYEOF'
import hashlib
import json
import os
import subprocess
import sys


def sh(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


def sha(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return ""


def find_bin(*names):
    for n in names:
        p = sh(["bash", "-lc", f"command -v {n}"]).strip()
        if p:
            return p
    return ""


def version(path, *flags):
    if not path:
        return ""
    out = sh([path] + list(flags))
    return out.splitlines()[0][:200] if out else ""


cp2k_bin = os.environ.get("AI2KIT_CP2K_BIN") or find_bin("cp2k.popt", "cp2k.psmp")
dp_bin = find_bin("dp")
ai2kit_bin = find_bin("ai2-kit")
omb_bin = find_bin("omb")
lmp_bin = find_bin("lmp")

manifest = {
    "root_seed": os.environ.get("AI2KIT_ROOT_SEED", ""),
    "ref_run_id": os.environ.get("AI2KIT_REF_RUN_ID", ""),
    "profile": os.environ.get("AI2KIT_PROFILE", ""),
    "slurm_job_id": os.environ.get("SLURM_JOB_ID", ""),
    "slurm_proc_id": os.environ.get("SLURM_PROCID", ""),
    "compute_host": sh(["hostname"]),
    "packages": {
        "deepmd_kit": version(dp_bin, "--version"),
        "ai2_kit": version(ai2kit_bin, "--version"),
        "oh_my_batch": version(omb_bin, "--version"),
        "lammps": version(lmp_bin, "-h"),
        "cp2k": version(cp2k_bin, "--version"),
    },
    "binary_sha256": {
        "dp": sha(dp_bin),
        "ai2_kit": sha(ai2kit_bin),
        "omb": sha(omb_bin),
        "lmp": sha(lmp_bin),
        "cp2k": sha(cp2k_bin),
    },
}
with open(sys.argv[1], "w") as f:
    json.dump(manifest, f, indent=2)
    f.write("\n")
print(f"[provenance] manifest -> {sys.argv[1]}")
PYEOF
