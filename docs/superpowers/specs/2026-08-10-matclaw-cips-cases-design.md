# MatClaw CIPS Cases 031–033 Design

## Goal

Construct three executable, open-source benchmark cases from the pinned MatClaw
paper and official repository:

- `031-matclaw-cips-active-distillation` (Task 1b)
- `032-matclaw-cips-curie-temperature` (Task 2b)
- `033-matclaw-cips-domain-wall-search` (Task 3)

The benchmark must distinguish a runnable workflow from a scientific
reproduction. A case may set `benchmark_valid: true` only after its paper profile
has run in the pinned image and the regenerated result agrees with the published
or official result within a documented tolerance.

## Authoritative inputs

All cases consume the already recovered provenance package at
`benchmark/sources/matclaw`:

- MatClaw paper: arXiv `2604.02688v3`, SHA-256 locked.
- Official repository: `release@52557c077f5e3be8444a3f03ea10a647fbd442ca`.
- Teacher potential: AIS Square record 109,
  `vdW_CuInP2S6_optB86b`, exact archive/model hashes locked.
- Structures: upstream `CuInP2S6.cif` and `cips_monolayer.cif`.
- Type map: `Cu In P S`.
- Task 3 field implementation: pinned `_efield_calculator.py`, using
  `F_total = F_DP + qE` and external energy `-sum(q r·E)` with the recovered
  effective charges.

Task 2 and Task 3 raw remote trajectories are absent upstream. Locally generated
trajectories must therefore be labelled `regenerated_from_pinned_protocol`, never
`upstream_repository`.

## Shared execution image

Add `base-env-build/matclaw-cips/Dockerfile` and a `matclaw-cips` target to
`base-env-build/build.sh`. The immutable scientific environment contains:

- CPython and the exact compatible DeePMD/TensorFlow runtime;
- ASE, NumPy, SciPy, matplotlib, pymatgen and dpdata;
- a LAMMPS executable with DeePMD pair support;
- the recovered teacher model and type map, copied from locked local sources.

The image gate verifies finite teacher inference, `pair_style deepmd`, a short
CIPS MD, trajectory parsing, and CPU execution. Case Dockerfiles only use
`FROM dftworld-base-matclaw-cips:<locked-tag>` and `COPY public/ /app/`.

## Common case contract

Each case contains `Dockerfile`, `instruction.md`, `task.toml`, `public/`,
`reference/`, `solution/`, `tests/`, `VALIDATION.json`, and
`benchmark_valid.json`. `reference/`, `solution/`, and `tests/` are never copied
into the agent workspace.

Every workflow accepts `--profile smoke|paper`:

- `smoke` proves wiring and evaluator behavior with reduced trajectory lengths;
  it always records `formal_result: false`.
- `paper` uses the scientific protocol and is the only profile allowed to write
  `regenerated_reference.json` or satisfy the formal reproduction gate.

All outputs carry provenance hashes, profile, random seed, software identity,
and enough raw data for the hidden evaluator to recompute the reported metric.

## 032: Curie temperature (implemented first)

The public inputs are the locked 10-atom bulk CIPS structure and teacher model.
The paper profile builds a `6x6x1` (360 atom) supercell, performs a convergence
pilot near the transition, runs a temperature sweep that brackets the phase
transition, and extends near-transition trajectories to the paper duration.

From every trajectory the workflow computes the Cu displacement relative to the
host midplane and the robust finite-temperature order parameter
`Q(T)=<|eta(t)|>`. The evaluator independently parses trajectories, recomputes
`Q(T)`, checks sampling/convergence, and recomputes the transition estimate.

Published target: `Tc = 261.3 ± 10.0 K`. The tolerance is not hardcoded merely
from the paper uncertainty: it is frozen after repeated paper-profile runs and
must cover measured seed/runtime variability while still rejecting a missing or
incorrect transition.

## 031: Active distillation

The public inputs additionally include the recovered He et al. paper. The paper
profile performs teacher MD at 100, 300, 500 and 800 K for at least 20 ps,
creates disjoint train/test sets, trains a real student ensemble, explores with
student MD, computes committee force deviation, labels informative structures
with the teacher, expands the training set, and retrains.

The selection bands are `<0.05`, `0.05–0.15`, and `>0.15 eV/Å` for skip,
label, and reject. Stop after a held-out force MAE below `0.10 eV/Å` or five
iterations. The evaluator checks model artifacts, independent set hashes,
teacher-bound labels, dataset growth, retraining, exploration diversity, and the
stopping claim.

Published successful-run reference: two active-learning iterations, 1226 total
training frames, and force MAE `0.098 eV/Å`. Iteration/frame counts are comparison
evidence, not exact pass requirements.

## 033: Domain-wall search

The public input is the locked monolayer structure and teacher model. The paper
profile constructs `1x25x1`, starts at `Ez=-0.01 V/Å, T=200 K`, stays within
`Ez in [0,-0.3] V/Å` and `T in [0,250] K`, and submits no more than two jobs per
search iteration. Each next point must be derived from prior measured results.

The evaluator recomputes Cu flip times, fits mean absolute time separation versus
site distance for distances 1–10, checks sequential propagation, and accepts a
measured slope above `0.3 ps/site`. It verifies that every run uses the recovered
field force/energy implementation.

Published best-run comparison: 14 jobs over seven iterations; best observed
point `Ez=-0.16 V/Å, T=50 K`; slope `0.321 ps/site`; 42/50 Cu sites flipped;
velocity about 640 m/s. Exact visited points are not hard gates.

## Acceptance and truth states

Each case records gates G0–G12: public isolation, paper/repository provenance,
hashes, prompt fidelity, original reference, image smoke, regenerated reference,
original/regenerated agreement, oracle pass, negative-fixture rejection,
alternative-valid acceptance, and characterized reproducibility.

Metadata uses these strict states:

1. `constructed`: files and static contracts exist.
2. `smoke_verified`: reduced workflow and evaluator execute.
3. `paper_reproduced`: the paper profile completed and its comparison report
   contains measured differences.
4. `benchmark_valid`: all G0–G12 pass.

No unrun gate is represented as true. A scientific mismatch remains a visible
failure with logs and measured values; reference tolerances are not widened to
make it pass.

