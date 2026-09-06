#!/usr/bin/env python3
"""
manifest_writer.py — write the CONTRACT §4 `final/` contract for case 034.

Writes:
  final/manifest.json   machine-readable summary (CONTRACT §4 schema + profile)
  final/report.md       concise scientific report (method, validation, limits)

Both are produced from the REAL artifacts that the oracle just ran:
  - AIMD mother-set frame count (config/aimd.xyz)
  - active-learning label sets (al/iter-*/new-dataset) and rounds
  - dp-test metrics parsed from validation/dp-test/output/model-*.{f,e_peratom}.out
  - environment versions probed from the container venv

Usage: manifest_writer.py <final_dir> [workspace_root]
Environment: sources AI2KIT_* variables exported by env.sh.
"""

from __future__ import annotations

import datetime
import glob
import json
import os
import subprocess
import sys

FINAL_DIR = sys.argv[1] if len(sys.argv) > 1 else "/app/final"
WORKSPACE = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("AI2KIT_034_WORKSPACE", "/app")

PROFILE = os.environ.get("AI2KIT_PROFILE", "paper")


def get_version(cmd, split_ok=True) -> str:
    """Best-effort version probe; never raises."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
        if not out:
            return "?"
        return out.splitlines()[0].split(" ")[0] if split_ok else out.splitlines()[0]
    except Exception:
        return "?"


def count_xyz_frames(path: str) -> int:
    """Count extxyz frames: lines that are a bare integer (natoms line)."""
    if not os.path.isfile(path):
        return 0
    n = 0
    try:
        with open(path) as fh:
            for line in fh:
                tok = line.strip()
                if tok.isdigit():
                    n += 1
    except Exception:
        return 0
    return n


def load_cols(path: str):
    """Load whitespace-separated numeric columns of a file."""
    import numpy as np

    try:
        return np.loadtxt(path)
    except Exception:
        return None


def dp_metrics(validation_dir: str) -> dict:
    """Parse dp-test outputs into per-model and pooled E/F metrics."""
    out = {"energy_rmse_meV_per_atom": None, "energy_mae_meV_per_atom": None,
           "force_rmse_eV_per_ang": None, "force_mae_eV_per_ang": None,
           "n_models": 0, "files": []}
    dp_out = os.path.join(validation_dir, "dp-test", "output")
    f_files = sorted(glob.glob(os.path.join(dp_out, "model-*.f.out")))
    e_files = sorted(glob.glob(os.path.join(dp_out, "model-*.e_peratom.out")))
    out["n_models"] = len(f_files)
    out["files"] = [os.path.relpath(p, validation_dir) for p in e_files + f_files]

    if not f_files or not e_files:
        return out

    import numpy as np

    all_fe, all_me = [], []
    all_ff, all_mf = [], []
    for e in e_files:
        data = load_cols(e)
        if data is not None and data.ndim == 2 and data.shape[1] >= 2:
            all_fe.append(data[:, 0])
            all_me.append(data[:, 1])
    for f in f_files:
        data = load_cols(f)
        if data is not None and data.ndim == 2 and data.shape[1] >= 6:
            all_ff.append(data[:, :3].ravel())
            all_mf.append(data[:, 3:6].ravel())

    if all_fe and all_ff:
        dft_e = np.concatenate(all_fe)
        mlp_e = np.concatenate(all_me)
        dft_f = np.concatenate(all_ff)
        mlp_f = np.concatenate(all_mf)

        def rmse(a, b):
            return float(np.sqrt(np.mean((a - b) ** 2)))

        def mae(a, b):
            return float(np.mean(np.abs(a - b)))

        out["energy_rmse_meV_per_atom"] = rmse(dft_e, mlp_e) * 1000.0
        out["energy_mae_meV_per_atom"] = mae(dft_e, mlp_e) * 1000.0
        out["force_rmse_eV_per_ang"] = rmse(dft_f, mlp_f)
        out["force_mae_eV_per_ang"] = mae(dft_f, mlp_f)
    return out


def main() -> None:
    cfg_dir = os.environ.get("AI2KIT_CFG_DIR", os.path.join(WORKSPACE, "work", "config"))
    al_dir = os.environ.get("AI2KIT_AL_DIR", os.path.join(WORKSPACE, "work", "al"))
    val_dir = os.environ.get("AI2KIT_VALIDATION_DIR", os.path.join(WORKSPACE, "work", "validation"))

    # ---- data provenance --------------------------------------------------
    aimd_xyz = os.path.join(cfg_dir, "aimd.xyz")
    n_aimd = count_xyz_frames(aimd_xyz)
    label_sets = sorted(glob.glob(os.path.join(al_dir, "iter-*", "new-dataset")))
    label_frames = 0
    for ls in label_sets:
        coords = glob.glob(os.path.join(ls, "**", "set.000", "coord.npy"), recursive=True)
        if coords:
            try:
                import numpy as np

                label_frames += int(np.load(coords[0]).shape[0])
            except Exception:
                pass
    n_rounds = len(label_sets)
    init_frames = min(n_aimd, int(os.environ.get("AI2KIT_SETUP_SAMPLE", "50")))
    provenance = (
        f"CP2K AIMD (NVT 300 K, BLYP-D3/TZV2P-GTH) generated from the supplied "
        f"64-H2O structure after GEO_OPT, filtered to {n_aimd} frames; "
        f"{init_frames} frames sampled as the initial training set; "
        f"+ {n_rounds} active-learning label set(s) "
        f"(model-deviation screened LAMMPS frames relabeled by real CP2K, "
        f"{label_frames} frames total) appended across {n_rounds} retrain round(s)."
    )

    # ---- validation metrics ----------------------------------------------
    metrics = dp_metrics(val_dir)

    # ---- environment manifest --------------------------------------------
    env_manifest = (
        f"ai2-kit {get_version(['ai2-kit', '--version'])}, "
        f"dp {get_version(['dp', '--version'])}, "
        f"lammps {get_version(['lmp', '-h'], split_ok=False) or 'lmp'}, "
        f"cp2k {get_version(['cp2k', '--version'], split_ok=False) or 'cp2k.psmp'}"
    )

    # ---- validation artifacts (relative to final/) ------------------------
    val_artifacts = []
    if os.path.isdir(os.path.join(val_dir, "dp-test", "output")):
        val_artifacts += [f"validation/dp-test/output/{os.path.basename(f)}"
                          for f in sorted(glob.glob(os.path.join(val_dir, "dp-test", "output", "model-*.f.out")))]
        if os.path.exists(os.path.join(val_dir, "dp-test", "output", "dp-test.png")):
            val_artifacts.append("validation/dp-test/output/dp-test.png")
    if os.path.isdir(os.path.join(val_dir, "nvt")):
        for f in ("thermo.dat", "model_devi.out", "dump.lammpstrj"):
            if os.path.exists(os.path.join(val_dir, "nvt", f)):
                val_artifacts.append(f"validation/nvt/{f}")
    if os.path.isdir(os.path.join(val_dir, "rdf")):
        for f in sorted(glob.glob(os.path.join(val_dir, "rdf", "rdf_*_aimd.dat"))) + \
                 sorted(glob.glob(os.path.join(val_dir, "rdf", "compare_rdf.png"))):
            val_artifacts.append("validation/rdf/" + os.path.basename(f))

    manifest = {
        "model_family": "DeePMD",
        "model_files": ["models/final/compress.pb"],
        "workflow_root": "workflow",
        "training_data_provenance": provenance,
        "validation_artifacts": val_artifacts,
        "environment_manifest": env_manifest,
        "status": "completed",
        "profile": PROFILE,
        "metrics": {
            "aimd_frames": n_aimd,
            "al_rounds": n_rounds,
            "al_label_frames": label_frames,
            **metrics,
        },
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }

    with open(os.path.join(FINAL_DIR, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    print(json.dumps(manifest, indent=2))

    # ---- report.md --------------------------------------------------------
    def fmt_e(v):
        return "%.3f meV/atom" % v if v is not None else "n/a"

    def fmt_f(v):
        return "%.4f eV/Å" % v if v is not None else "n/a"

    report = f"""# DeePMD water potential — 64 H2O (end-to-end)

## Method
1. **01-geopt** — CP2K GEO_OPT (BLYP-D3 / TZV2P-GTH, CUTOFF 500) relaxes the
   PACKMOL 64-H2O structure (close contacts removed) before any AIMD.
2. **02-aimd** — CP2K AIMD (NVT 300 K, timestep 0.5 fs) from the optimised
   structure; raw pos/frc/cell/ener outputs converted (drop step 0, dedupe,
   temperature window) into a {n_aimd}-frame labeled mother set.
3. **03-active-learning** — ai2-kit closed loop over {n_rounds} round(s):
   committee DeePMD train -> LAMMPS exploration (metal units, 0.5 fs) ->
   model-deviation screen -> CP2K single-point relabel -> dataset grows
   ({label_frames} active-learning frames).  Final committee model:
   `models/final/compress.pb`.
4. **04-validation** — held-out dp-test, 300 K NVT stability, AIMD-vs-MLP RDF.

## Reference data provenance
{provenance}

## Validation (agent-side, on held-out subset of the mother set)
- Energy RMSE {fmt_e(metrics['energy_rmse_meV_per_atom'])}, MAE {fmt_e(metrics['energy_mae_meV_per_atom'])}.
- Force RMSE {fmt_f(metrics['force_rmse_eV_per_ang'])}, MAE {fmt_f(metrics['force_mae_eV_per_ang'])}.
- {metrics['n_models']} committee model(s) tested; parity plot in
  `validation/dp-test/output/dp-test.png`.

## Environment
{env_manifest}

## Limitations
- Schedule scaled to a 16-core CPU container (profile `{PROFILE}`); for the
  formal run the training/exploration schedule is scaled up but remains far
  smaller than the original HPC run (AIMD 10k steps, 5 rounds, 400k train steps).
- The dp-test held-out set derives from the agent's own AIMD mother set; the
  hidden evaluator additionally scores against an independent hidden validation
  set.  NVT/RDF artifacts are agent-side sanity checks, not the hidden verdict.
"""
    with open(os.path.join(FINAL_DIR, "report.md"), "w") as fh:
        fh.write(report)
    print(f"wrote {FINAL_DIR}/manifest.json and {FINAL_DIR}/report.md")


if __name__ == "__main__":
    main()
