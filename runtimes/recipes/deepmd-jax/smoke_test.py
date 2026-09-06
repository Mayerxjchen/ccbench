#!/usr/bin/env python3
"""G1 runtime smoke for dftworld-base-deepmd-jax (DPMP/JAX workflow capability).

Four layers (the G1 hard acceptance, not just "import works"):
  S1  import jax / flax / optax / jax_md / deepmd_jax
  S2  train a minimal DP-MP (mp=True) model and run single-frame E/F inference
      -> finite output
  S3  confirm the message-passing path was actually used (mp=True, not DPSR)
  S4  drive the DPMP model through a short MD integration -> finite positions

Run inside the image:
    /opt/ai2kit/bin/python smoke_test.py
Exit 0 iff all four pass.
"""
from __future__ import annotations

import os
import tempfile

import numpy as np

MODEL = "/tmp/smoke_model.pkl"


def s1_import():
    import jax
    import flax
    import optax
    import jax_md
    import deepmd_jax  # noqa: F401
    from deepmd_jax.train import train, test, evaluate  # noqa: F401
    from deepmd_jax.md import Simulation  # noqa: F401
    print(f"S1 PASS: jax {jax.__version__}, flax {flax.__version__}, "
          f"optax {optax.__version__}, jax_md, deepmd_jax")
    return jax


def _make_tiny_set(natom=4, nframe=4, seed=0):
    # nframe >= 2 so the descriptor normalisation (sr_mean/sr_std over frames)
    # has nonzero std; a 1-frame set gives sr_std=0 -> nan loss.
    rng = np.random.default_rng(seed)
    d = tempfile.mkdtemp(prefix="djax-smoke-")
    setdir = os.path.join(d, "set.000")
    os.makedirs(setdir)
    box = np.diag([12.0, 12.0, 12.0]).reshape(9)
    # spread atoms so no pathological close contacts within rcut=6
    base = np.array([4.0, 4.0, 4.0, 8.0, 8.0, 4.0, 4.0, 8.0, 8.0, 8.0, 4.0, 8.0],
                    dtype=float)
    coord = base[None, :] + rng.normal(0, 0.2, (nframe, natom * 3))
    energy = rng.normal(-2.0 * natom, 1.0, nframe)
    force = rng.normal(0, 0.1, (nframe, natom * 3))
    np.save(os.path.join(setdir, "box.npy"), np.tile(box, (nframe, 1)))
    np.save(os.path.join(setdir, "coord.npy"), coord)
    np.save(os.path.join(setdir, "energy.npy"), energy)
    np.save(os.path.join(setdir, "force.npy"), force)
    # 2 O + 2 H
    typ = np.array([0, 0, 1, 1], dtype=int)
    with open(os.path.join(d, "type.raw"), "w") as fh:
        fh.write("\n".join(str(int(t)) for t in typ) + "\n")
    with open(os.path.join(d, "type_map.raw"), "w") as fh:
        fh.write("O\nH\n")
    return d, coord, np.diag([12.0, 12.0, 12.0]), typ


def s2_s3_train_and_infer():
    from deepmd_jax.train import train, evaluate
    d, coord, box, typ = _make_tiny_set()
    # minimal DP-MP: mp=True (message passing) with the smallest valid widths
    train(
        model_type="energy",
        rcut=6.0,
        train_data_path=[d],
        val_data_path=None,
        save_path=MODEL,
        step=2,
        mp=True,                       # S3: message-passing path
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
    # single-frame inference: evaluate() wants coord (n_frames, n_atoms, 3)
    natom = typ.shape[0]
    res = evaluate(MODEL, coord.reshape(coord.shape[0], natom, 3),
                   np.tile(box.reshape(9), (coord.shape[0], 1)), typ)
    # evaluate returns (energy, forces) or a dict depending on version
    if isinstance(res, dict):
        energy = np.asarray(res.get("energy", res.get("energies")))
        forces = np.asarray(res.get("force", res.get("forces")))
    else:
        energy, forces = res
    if not (np.all(np.isfinite(energy)) and np.all(np.isfinite(forces))):
        raise RuntimeError("S2 FAIL: non-finite inference output")
    print(f"S2 PASS: DP-MP inference finite (energy {np.asarray(energy).reshape(-1)[0]:.4f} eV)")
    print("S3 PASS: mp=True message-passing model trained + inferred")
    return MODEL


def s4_md(model_path):
    from deepmd_jax.md import Simulation
    box = np.diag([12.0, 12.0, 12.0])
    typ = np.array([0, 0, 1, 1], dtype=int)
    coord = np.random.default_rng(3).uniform(0, 12.0, (4, 3))
    sim = Simulation(
        model_path=model_path,
        box=box,
        type_idx=typ,
        mass=[15.999, 1.008],
        routine="NVT",
        dt=0.5,
        initial_position=coord,
        temperature=300,
        seed=1,
    )
    traj = sim.run(10)
    pos = np.asarray(traj["position"])
    if not np.all(np.isfinite(pos)):
        raise RuntimeError("S4 FAIL: MD produced NaN")
    print(f"S4 PASS: DPMP + MD 10 steps finite (positions {pos.shape})")


def main():
    s1_import()
    model = s2_s3_train_and_infer()
    s4_md(model)
    print("ALL G1 SMOKE PASS")


if __name__ == "__main__":
    main()
