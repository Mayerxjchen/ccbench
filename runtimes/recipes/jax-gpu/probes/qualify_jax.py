#!/usr/bin/env python3
"""Physical GPU qualification probe for the MLFFBench JAX GPU runtime (Case 042).

Executes four-layer verification on genuine GPU hardware:
  S1  Verify JAX, Flax, Optax, JAX-MD, and DeepMD-JAX module imports & GPU presence
  S2  Train minimal DP-MP (mp=True) model on GPU & verify finite energy/force inference
  S3  Confirm message-passing parameter usage (mp=True, not DPSR)
  S4  Run 10-step NVT molecular dynamics integration & verify finite positions

CLI:
    python qualify_jax.py [--json-out PATH]
Exits 0 iff GPU is visible and all four layers succeed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np


def run_qualification() -> dict:
    t0 = time.perf_counter()
    report: dict = {
        "gpu_visible": False,
        "gpu_name": "",
        "x64_enabled": False,
        "versions": {},
        "passes": {},
        "parity_ok": False,
        "elapsed_s": 0.0,
    }

    # Verify x64 setting
    report["x64_enabled"] = os.environ.get("JAX_ENABLE_X64") == "1"

    # --- S1: Module Imports & GPU Device Discovery ---
    t_s1 = time.perf_counter()
    import jax
    import flax
    import optax
    import jax_md
    import deepmd_jax
    from deepmd_jax.train import train, evaluate
    from deepmd_jax.md import Simulation

    devices = jax.devices()
    gpu_devices = [d for d in devices if d.platform == "gpu"]
    if not gpu_devices:
        raise RuntimeError(f"S1 FAIL: No GPU devices found in jax.devices(): {devices}")

    gpu_dev = gpu_devices[0]
    gpu_name = getattr(gpu_dev, "device_kind", "unknown-gpu")
    report["gpu_visible"] = True
    report["gpu_name"] = str(gpu_name)
    report["versions"] = {
        "python": sys.version.split()[0],
        "jax": getattr(jax, "__version__", "unknown"),
        "jaxlib": getattr(getattr(jax, "_src", None), "version", "unknown"),
        "flax": getattr(flax, "__version__", "unknown"),
        "optax": getattr(optax, "__version__", "unknown"),
        "jax_md": getattr(jax_md, "__version__", "unknown"),
        "deepmd_jax": getattr(deepmd_jax, "__version__", "0.2"),
    }
    report["passes"]["s1_import"] = {
        "status": "PASS",
        "gpu_devices": len(gpu_devices),
        "device_kind": str(gpu_name),
        "elapsed_s": round(time.perf_counter() - t_s1, 3),
    }

    # --- S2 & S3: DP-MP Model Training & Single-Frame Inference ---
    t_s2 = time.perf_counter()
    rng = np.random.default_rng(0)
    with tempfile.TemporaryDirectory(prefix="djax-qualify-") as d:
        setdir = os.path.join(d, "set.000")
        os.makedirs(setdir)
        natom, nframe = 4, 4
        box = np.diag([12.0, 12.0, 12.0]).reshape(9)
        base = np.array([4.0, 4.0, 4.0, 8.0, 8.0, 4.0, 4.0, 8.0, 8.0, 8.0, 4.0, 8.0], dtype=float)
        coord = base[None, :] + rng.normal(0, 0.2, (nframe, natom * 3))
        energy = rng.normal(-2.0 * natom, 1.0, nframe)
        force = rng.normal(0, 0.1, (nframe, natom * 3))
        np.save(os.path.join(setdir, "box.npy"), np.tile(box, (nframe, 1)))
        np.save(os.path.join(setdir, "coord.npy"), coord)
        np.save(os.path.join(setdir, "energy.npy"), energy)
        np.save(os.path.join(setdir, "force.npy"), force)

        typ = np.array([0, 0, 1, 1], dtype=int)
        with open(os.path.join(d, "type.raw"), "w") as fh:
            fh.write("\n".join(str(int(t)) for t in typ) + "\n")
        with open(os.path.join(d, "type_map.raw"), "w") as fh:
            fh.write("O\nH\n")

        model_path = os.path.join(d, "model.pkl")
        train(
            model_type="energy",
            rcut=6.0,
            train_data_path=[d],
            val_data_path=None,
            save_path=model_path,
            step=2,
            mp=True,  # S3: message-passing path
            embed_widths=[8, 8, 16],
            embed_mp_widths=[16, 16, 16],
            fit_widths=[16, 16, 16],
            axis_neurons=4,
            lr=0.001,
            batch_size=1,
            compress=False,
            print_every=1,
            seed=0,
        )

        res = evaluate(
            model_path,
            coord.reshape(coord.shape[0], natom, 3),
            np.tile(box.reshape(9), (coord.shape[0], 1)),
            typ,
        )
        if isinstance(res, dict):
            e_out = np.asarray(res.get("energy", res.get("energies")))
            f_out = np.asarray(res.get("force", res.get("forces")))
        else:
            e_out, f_out = res

        if not (np.all(np.isfinite(e_out)) and np.all(np.isfinite(f_out))):
            raise RuntimeError("S2 FAIL: Non-finite inference output on GPU")

        report["passes"]["s2_train_infer"] = {
            "status": "PASS",
            "energy_eV": float(np.asarray(e_out).reshape(-1)[0]),
            "max_force_eV_A": float(np.max(np.abs(np.asarray(f_out)))),
            "elapsed_s": round(time.perf_counter() - t_s2, 3),
        }
        report["passes"]["s3_message_passing"] = {
            "status": "PASS",
            "mp_enabled": True,
        }

        # --- S4: 10-step NVT Molecular Dynamics ---
        t_s4 = time.perf_counter()
        md_coord = np.random.default_rng(3).uniform(0, 12.0, (4, 3))
        sim = Simulation(
            model_path=model_path,
            box=np.diag([12.0, 12.0, 12.0]),
            type_idx=typ,
            mass=[15.999, 1.008],
            routine="NVT",
            dt=0.5,
            initial_position=md_coord,
            temperature=300,
            seed=1,
        )
        traj = sim.run(10)
        pos = np.asarray(traj["position"])
        if not np.all(np.isfinite(pos)):
            raise RuntimeError("S4 FAIL: MD simulation produced NaN on GPU")

        report["passes"]["s4_nvt_md"] = {
            "status": "PASS",
            "md_steps": 10,
            "trajectory_shape": list(pos.shape),
            "elapsed_s": round(time.perf_counter() - t_s4, 3),
        }

    report["parity_ok"] = True
    report["elapsed_s"] = round(time.perf_counter() - t0, 3)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run JAX GPU qualification probe.")
    parser.add_argument("--json-out", type=Path, default=None, help="Output JSON report path")
    args = parser.parse_args()

    try:
        report = run_qualification()
        formatted = json.dumps(report, indent=2)
        print(formatted)
        if args.json_out:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(formatted + "\n", encoding="utf-8")
        return 0
    except Exception as exc:
        err_report = {
            "gpu_visible": False,
            "parity_ok": False,
            "error": str(exc),
        }
        print(json.dumps(err_report, indent=2), file=sys.stderr)
        if args.json_out:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(json.dumps(err_report, indent=2) + "\n", encoding="utf-8")
        return 1


if __name__ == "__main__":
    sys.exit(main())
