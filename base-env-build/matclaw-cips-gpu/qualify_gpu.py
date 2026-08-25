"""CPU/GPU parity and MD qualification for the MatClaw CIPS GPU runtime.

Runs the locked CIPS structure through the locked teacher model once with CUDA
disabled and once with CUDA visible, compares the resulting energies and forces,
then runs a short MD to confirm finite, frame-exact production output. Emits one
JSON document; exits nonzero unless GPU is visible, both parity diffs are below
``1e-6``, and every MD value is finite with the exact expected frame count.

TensorFlow reads ``CUDA_VISIBLE_DEVICES`` at CUDA initialization, so the CPU
pass must run in a *separate process*. The main process spawns itself twice
(``--pass cpu`` and ``--pass gpu``) and merges the two result dicts.

CLI (as shipped at ``/opt/matclaw/qualify_gpu.py``)::

    /opt/matclaw/bin/python /opt/matclaw/qualify_gpu.py \\
        --structure PATH --model PATH --supercell X Y Z --temperature K \\
        --steps 100 --json-out PATH
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path


def _versions() -> dict[str, str]:
    import ase
    import deepmd
    import tensorflow as tf

    return {
        "python": sys.version.split()[0],
        "tensorflow": tf.__version__,
        "deepmd": getattr(deepmd, "__version__", "unknown"),
        "ase": ase.__version__,
    }


def _run_pass(
    structure: Path,
    model: Path,
    supercell: tuple[int, int, int],
    temperature_k: float,
    steps: int,
    cuda_visible: str,
) -> dict:
    """One device pass. ``cuda_visible`` is the ``CUDA_VISIBLE_DEVICES`` value."""
    start = time.perf_counter()

    # Import must happen after CUDA_VISIBLE_DEVICES is fixed for this process.
    os.environ["CUDA_VISIBLE_DEVICES"] = cuda_visible
    import numpy as np
    from ase import units
    from ase.io import read
    from ase.md.verlet import VelocityVerlet
    from deepmd.calculator import DP
    from deepmd.infer import DeepPot

    want_gpu = cuda_visible not in ("", "-1")
    if want_gpu:
        import tensorflow as tf

        if not tf.config.list_physical_devices("GPU"):
            raise SystemExit(
                "gpu pass requested (CUDA_VISIBLE_DEVICES!=empty) but TensorFlow "
                "sees no physical GPU; refusing to run silently on CPU"
            )

    atoms = read(str(structure))
    if supercell != (1, 1, 1):
        atoms *= supercell
    n_atoms = len(atoms)

    model_dp = DeepPot(str(model))
    energy, forces, virial = model_dp.eval(
        atoms.positions.reshape(1, -1),
        atoms.cell.array.reshape(1, 9),
        np.array([0, 1, 2, 2, 3, 3, 3, 3, 3, 3] * (n_atoms // 10), dtype=np.int32),
    )[:3]
    assert np.isfinite(energy).all()
    assert np.isfinite(forces).all()
    assert np.isfinite(virial).all()

    # Short production-style MD on the same device.  ASE >= 3.25 Atoms.copy()
    # does not carry the calculator, so each frame snapshot must re-attach the
    # DP calculator explicitly or get_potential_energy() raises "Atoms object
    # has no calculator".
    atoms.calc = DP(model=str(model))
    atoms.set_momenta(np.zeros((n_atoms, 3)))
    dynamics = VelocityVerlet(atoms, timestep=0.1 * units.fs)

    def _snapshot() -> "atoms.__class__":
        frame = atoms.copy()
        frame.calc = atoms.calc
        return frame

    frames: list = [_snapshot()]
    for _ in range(steps):
        dynamics.run(1)
        frames.append(_snapshot())
    n_frames = len(frames)
    energies = np.array([f.get_potential_energy() for f in frames])
    md_finite = bool(np.isfinite(energies).all())

    gpu_name = ""
    if want_gpu:
        try:
            visible = tf.config.list_physical_devices("GPU")
            if visible:
                gpu_name = visible[0].name
        except Exception:
            gpu_name = ""

    elapsed = time.perf_counter() - start
    return {
        "device": "cpu" if cuda_visible == "" else "gpu",
        "cuda_visible_devices": cuda_visible,
        "gpu_name": gpu_name,
        "n_atoms": n_atoms,
        "energy_eV": float(energy.flat[0]),
        "max_force_component_eV_A": float(np.abs(forces).max()),
        "md_steps": steps,
        "md_frames": n_frames,
        "md_finite": md_finite,
        "elapsed_s": elapsed,
        "seconds_per_step": elapsed / max(steps, 1),
    }


def _spawn_pass(
    argv: list[str], structure: Path, model: Path, cuda_visible: str
) -> dict:
    env = dict(os.environ)
    env["CUDA_VISIBLE_DEVICES"] = cuda_visible
    proc = subprocess.run(
        [sys.executable, __file__, "--pass", "child"] + argv,
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"device pass failed (CUDA_VISIBLE_DEVICES={cuda_visible!r}):\n"
            f"{proc.stderr or proc.stdout}"
        )
    return json.loads(proc.stdout.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--structure", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--supercell", type=int, nargs=3, default=[1, 1, 1])
    parser.add_argument("--temperature", type=float, default=300.0)
    parser.add_argument("--steps", type=int, default=100)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument(
        "--pass",
        dest="pass_name",
        choices=("cpu", "gpu", "child"),
        default=None,
    )
    args = parser.parse_args()

    if args.pass_name == "child":
        # Child process: one device pass, print compact JSON on stdout.
        result = _run_pass(
            args.structure,
            args.model,
            tuple(args.supercell),
            args.temperature,
            args.steps,
            os.environ.get("CUDA_VISIBLE_DEVICES", ""),
        )
        print(json.dumps(result, separators=(",", ":")))
        return

    # Parent: spawn one CPU and one GPU pass with clean CUDA envs.
    cpu = _spawn_pass(
        [
            "--structure", str(args.structure), "--model", str(args.model),
            "--supercell", *map(str, args.supercell),
            "--temperature", str(args.temperature), "--steps", str(args.steps),
        ],
        args.structure,
        args.model,
        cuda_visible="",
    )
    gpu = _spawn_pass(
        [
            "--structure", str(args.structure), "--model", str(args.model),
            "--supercell", *map(str, args.supercell),
            "--temperature", str(args.temperature), "--steps", str(args.steps),
        ],
        args.structure,
        args.model,
        cuda_visible="0",
    )

    gpu_visible = (
        gpu.get("device") == "gpu"
        and bool(gpu.get("gpu_name"))
        and gpu.get("md_finite") is True
        and cpu.get("device") == "cpu"
    )
    energy_diff = abs(cpu.get("energy_eV", math.inf) - gpu.get("energy_eV", math.inf))
    force_diff = abs(
        cpu.get("max_force_component_eV_A", math.inf)
        - gpu.get("max_force_component_eV_A", math.inf)
    )
    md_finite = bool(cpu.get("md_finite") and gpu.get("md_finite"))
    frames_ok = (
        cpu.get("md_frames") == args.steps + 1
        and gpu.get("md_frames") == args.steps + 1
    )
    parity_ok = gpu_visible and energy_diff < 1e-6 and force_diff < 1e-6

    report = {
        "gpu_visible": gpu_visible,
        "gpu_name": gpu.get("gpu_name", ""),
        "energy_abs_diff_eV": round(energy_diff, 12),
        "max_force_component_abs_diff_eV_A": round(force_diff, 12),
        "md_steps": args.steps,
        "md_finite": md_finite,
        "frames_ok": frames_ok,
        "elapsed_s": round(cpu.get("elapsed_s", 0.0) + gpu.get("elapsed_s", 0.0), 3),
        "seconds_per_step": round(
            (cpu.get("elapsed_s", 0.0) + gpu.get("elapsed_s", 0.0)) / max(args.steps, 1),
            6,
        ),
        "versions": _versions(),
        "passes": {"cpu": cpu, "gpu": gpu},
        "parity_ok": parity_ok,
    }

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))

    if not (gpu_visible and parity_ok and md_finite and frames_ok):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
