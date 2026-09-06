# C-gate runtime smoke evidence (Acceptance Board C1-C5)

**When / how**: 2026-08-10, ran inside the final base image
`dftworld-base-ai2kit:0.1.0-cpu` (image id `8a840aa2e477`) via:

```
docker run --rm -v "$PWD/tools":/tools -v /tmp/c-smoke:/tmp/c-smoke \
  dftworld-base-ai2kit:0.1.0-cpu bash -lc 'bash /tools/container_smoke_c.sh'
```

Driver: `tools/container_smoke_c.sh` (frozen in the case). Host is
Darwin/arm64; the cp2k/cp2k base image is amd64, executed under Rosetta.

**Result**: `=== C_GATES_ALL_PASS ===` (exit 0). Full transcript in
`smoke.log`.

## C1 scientific stack — `c1-versions.txt`
Real `--help/--version` output captured in-container:
- ai2-kit 1.1.0 (ai2-kit --help)
- DeePMD-kit v2.2.11 (`dp --version`)
- omb 0.7.6 (oh-my-batch; `--version` unsupported by omb, help captured, rc=2)
- LAMMPS 2 Aug 2023 - Update 3 (`lmp -help`)
- CP2K version 2025.2 (`cp2k --version`)
- python 3.11.15 venv /opt/ai2kit
- imports ok: ase, dpdata, pymatgen, MDAnalysis, deepmd, ai2_kit

## C2 cp2k ENERGY/FORCE — `c2/`
Real CP2K Quickstep BLYP-D3/TZV2P-MOLOPT-GTH job on 1 H2O in a 12.4 A cell.
SCF converged, ENERGY| Total FORCE_EVAL + forces written (`energy-force.txt`).

## C3 deepmd train/freeze/infer
Gated at image build time by `base-env-build/ai2kit/smoke_test.py`
(`[smoke] ALL CHECKS PASSED`), including DeepPot.eval finite E/F. C4 below
re-trains a toy model in-container and runs a deepmd MD, so C3's dp
train→freeze→infer path is exercised twice.

## C4 lammps+deepmd MD — `c4/`
`dp train` on a 2-frame toy set (stop_batch=1, v2 config) → `dp freeze` →
`frozen.pb` → `lmp -in in.lmp` driving `pair_style deepmd frozen.pb`
(units metal, run 30). `frames.txt` = trajectory timestep count; no NaN in
`dump.lammpstrj`. LAMMPS driven through the in-process `lmp` wrapper
(`/opt/lmp-wrapper/`) because the deepmd-kit pip launcher ELF segfaults under
Rosetta.

## C5 pseudo-slurm E2E — `c5/`
`sbatch --parsable hello.slurm` → job 2 → `sacct` COMPLETED. State DB at
`/root/.pseudo_slurm/jobs.jsonl` (STATE_DIR `~` expanded correctly — the
`job_db.py` tilde fix).

## Regeneration
Rerun the `docker run` command above; the driver script is the source of
truth, this README + files are the frozen transcript.
