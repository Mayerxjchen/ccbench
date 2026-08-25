# 034 CONTRACT — end-to-end DeePMD water-potential development

Case: `034-ai2kit-water64-end-to-end-potential`
Formal execution backend: **real_hpc_controller**. PAgent runs in an isolated
sandbox as the control layer and discovers/uses a real remote Slurm cluster.
The containerized pseudo-Slurm remains a developer test backend only and is not
valid evidence for a formal PAgent pilot or formal ablation run.

The agent starts from a **target specification with no atomic coordinates** and must,
by itself, generate a reproducible 64-H2O periodic structure and run the full
development cycle: prepare the structure, generate CP2K first-principles
data, train a DeePMD potential, iteratively improve it, and demonstrate predictive +
physical reliability. Methods are open; only the scientific goal and two abstract
stage requirements are fixed (see `instruction.md`).

This file is the single source of truth for paths, names, and the output contract.
All builders (base image, solution, verifier) MUST agree with it.

---

## 1. Directory layout (repo)

```
034-ai2kit-water64-end-to-end-potential/
├── CONTRACT.md
├── Dockerfile                  # FROM dftworld-base-ai2kit:<tag> ; COPY public/ /app/
├── instruction.md              # end-to-end prompt: stages fixed, methods open
├── task.toml                   # execution_backend=real_hpc_controller
├── public/                     # the ONLY thing the agent sees (via COPY public/ /app/)
│   └── system.json             # target identity only; contains no coordinates
├── reference/                  # hidden from agent
│   ├── expert-trajectory/      # hidden 5-stage lineage (provenance):
│   │   ├── initial/            #   PACKMOL structure (source of public water64.xyz)
│   │   ├── geopt/              #   CP2K GEO_OPT of the initial structure (394 steps)
│   │   ├── aimd/               #   CP2K AIMD raw outputs + 190-frame mother set
│   │   ├── active-learning/    #   ai2-kit CLL loop (run.sh, workflow, config, screening)
│   │   └── validation/         #   dp-test / NVT / RDF tools + metrics
│   ├── hidden-validation/      # dft-validation.extxyz + rdf-reference.json + manifest
│   │   └── generator/          # generate_hidden_validation.py (no HPC needed)
│   ├── reference.json          # expert metrics (machine-readable)
│   ├── thresholds.json         # draft scientific thresholds (freeze after reruns)
│   └── source.lock.json        # provenance + hashes + backend declaration
├── solution/
│   └── expert/                 # container-adapted runnable expert workflow
│       ├── 01-geopt/           #   stage 1: relax the initial structure
│       ├── 02-aimd/            #   stage 2: generate first-principles reference data
│       ├── 03-active-learning/ #   stage 3: iterative potential improvement
│       ├── 04-validation/      #   stage 4: scientific validation
│       ├── run.sh              #   orchestrator
│       └── README.md
├── tests/                      # hidden evaluator (staged into /tests at verify time)
│   ├── test.sh
│   ├── verifier.py             # L0–L9 outcome-based verifier
│   ├── test_outputs.py         # oracle + negative/alt-valid fixtures
│   ├── hidden/                 # REAL hidden set (committed from reference/hidden-validation)
│   └── fixtures/               # synthetic fixtures for local smoke
├── VALIDATION.json             # G0–G16 gates (honest pass/pending)
└── benchmark_valid.json
```

Base image build: `base-env-build/ai2kit/` (Dockerfile + pseudo-slurm/ + build.sh entry).

---

## 2. Public data (agent-visible)

Exactly one file is seeded under `/app`; the instruction is supplied separately
by the harness. No coordinate, cluster recipe, module name, executable path,
partition, remote workspace, credential, expert artifact, or hidden reference is
agent-visible:

| file | role |
|------|------|
| `system.json` | target specification: 64 H2O, 192 atoms, 12.4 Å periodic cubic cell, DeePMD family. It contains no positions or coordinate payload. |

The agent is told the system is 64 H2O, that CP2K is the DFT engine, that ai2-kit
must materially participate, and that the ML potential family is DeePMD. It must
generate every atomic coordinate and retain structure-generation provenance.
The sandbox exposes a discoverable remote-HPC capability without public connection
instructions; all cluster facts and scientific methods remain the agent's to discover.

---

## 3. Runtime software (inside the container, provided by dftworld-base-ai2kit)

- venv at `/opt/ai2kit` (python3.11); `/opt/ai2kit/bin` on PATH.
- Binaries the agent may use: `ai2-kit`, `omb` (from oh-my-batch), `dp`,
  `lmp` (deepmd-kit[lmp], LAMMPS_PLUGIN_PATH set), `cp2k` / `cp2k.psmp`,
  `python` (ase, dpdata, numpy, scipy, pymatgen, matplotlib, tensorflow).
- pseudo-slurm on PATH: `sbatch`, `squeue`, `sacct`, `scancel`, `sinfo`.
  It emulates a Slurm cluster backed by a JSONL job database (see below). It is a
  real job lifecycle, NOT `sbatch = bash`.
- `~/.pseudo_slurm/` is the scheduler state dir; `PSEUDO_SLURM_DIR` may override.

### pseudo-slurm contract (must satisfy oh-my-batch 0.7.6 Slurm backend)

oh-my-batch calls, in order:
1. `sbatch [opts] <script>` → runs `<script>` in the background from its own
   directory, prints `Submitted batch job <ID>` (an integer). Supports `--parsable`
   (print only the ID). Sets SLURM_JOB_ID / SLURM_SUBMIT_DIR / SLURM_NTASKS /
   SLURM_CPUS_PER_TASK env for the child. Records {id, script, submit_dir, pid,
   start, state} in the job database.
2. oh-my-batch injects an exit-code logger into `<script>` BEFORE calling sbatch:
   it appends `echo $EXIT_CODE > "<script>.exitcode"`. Do NOT strip or rewrite this;
   the `.exitcode` file is the completion signal.
3. `sacct -X -P --format=JobID,State -j <id1,id2,...>` → CSV with header `JobID|State`
   and full state names: PENDING / RUNNING / COMPLETED / FAILED / CANCELLED.
   State resolution: process alive → RUNNING; else `<script>.exitcode` == `0` →
   COMPLETED; else FAILED; unknown job id → omit row (omb falls back to squeue).
4. `squeue -h -o "%A %t"` → lines `JobID <state-letter>` for non-terminal jobs.
   State letters (Slurm codes omb maps): PD, R, CG, CF, CA, F, TO, NF, RV, SE, CD.
5. `scancel <id>` → kill process, set CANCELLED.
6. `sinfo` → simple partition/state table (partition names `gpu,cpu`).

A job process must be reaped and its real exit code recorded so `sacct` can answer
after the child dies even if `.exitcode` is absent (agent-written scripts without
the omb injection).

---

## 4. Agent output contract (`/app/final/`)

The agent decides internal layout EXCEPT these fixed names:

```
final/
├── manifest.json          # machine-readable summary
├── workflow/              # reproducible workflow (scripts + logs)
├── models/                # final trained DeepMD model file(s)
├── provenance/            # data provenance (what went into training, incl. all
│                          #   first-principles data generated during the project)
├── validation/            # optional: agent's own dp-test/nvt/rdf artifacts
└── report.md              # concise scientific report
```

`manifest.json` schema:

```json
{
  "model_family": "DeePMD",
  "structure_generation": {
    "initial_structure": "work/initial/generated.extxyz",
    "generator": "agent-selected structure builder",
    "generator_version": "recorded version",
    "command": "reproducible generation command",
    "seed": 41,
    "checks": {"composition": "PASS", "periodic_geometry": "PASS"},
    "initial_structure_sha256": "64 lowercase hex characters",
    "cp2k_input_structure_sha256": "the same 64-character identity"
  },
  "model_files": ["models/final/compress.pb"],
  "workflow_root": "workflow",
  "training_data_provenance": "CP2K AIMD generated from the supplied structure (...frames) + N active-learning label sets (...)",
  "validation_artifacts": ["validation/dp-test/..."],
  "environment_manifest": "ai2-kit X, dp X, lammps X, cp2k X",
  "status": "completed"
}
```

`model_files` are workspace-relative paths to frozen DeepMD graphs (compress.pb /
frozen_model.pb / graph.pb). At least one must exist, be loadable by `dp`, and be
referenced. The verifier NEVER requires any particular directory names beyond the
`final/` skeleton above (no `iter-*`, no `screening/`, no `model_devi.out` required).
The verifier does NOT trust the manifest's prose; every claim is cross-checked
against workspace artifacts (see §5 L0–L6).

---

## 5. Hidden validation set + verifier design (tests/, outcome-based)

### Hidden set (no HPC required)

- `reference/hidden-validation/dft-validation.extxyz`: the **190-frame expert AIMD
  mother set** (energy + forces, extxyz). The public input has no coordinates;
  the agent generates its own structure and AIMD and therefore does not receive
  these frames → fair out-of-training test.
- `reference/hidden-validation/rdf-reference.json`: O-O/O-H/H-H RDF first peaks +
  curves computed from those same 190 frames.
- Generated by `generator/generate_hidden_validation.py`; committed copies live in
  `tests/hidden/`. Staged into `/tests` ONLY by eval.verify(); the agent never sees
  them. Paths injected via task.toml `[verifier.env]`
  (`AI2KIT_HIDDEN_VALIDATION=/tests/hidden/dft-validation.extxyz`,
  `AI2KIT_HIDDEN_RDF_REFERENCE=/tests/hidden/rdf-reference.json`).

### Verifier (tests/verifier.py, `verify(submission, profile) -> dict`)

`test.sh`: run `/opt/ai2kit/bin/python -m pytest -q /tests/test_outputs.py -rA`
then `echo 1|0 > /logs/verifier/reward.txt` (mirror 031 test.sh).

The verifier locates the submission at `$AI2KIT_034_SUBMISSION` (default `/app`;
dev smoke runs point it at a fixture dir). It never inspects anything outside the
workspace except the staged hidden files under `/tests/hidden/`.

**L0 autonomous structure generation.** `manifest.json.structure_generation`
records the generated structure, generator/version, exact command, seed, checks,
and SHA-256 linkage to the CP2K handoff. The referenced periodic XYZ must contain
exactly 64 intact H2O molecules in the 12.4 Å cubic cell, with sane O-H bonds,
H-O-H angles, and intermolecular contacts. Its bytes must not match a denied
hidden/reference identity. Coordinates are validated by geometry and provenance,
not by requiring equality to the expert structure. Benchmark-only dual-reference
calibration sets `AI2KIT_REFERENCE_MODE=1` to bypass this agent-only gate; formal
PAgent evaluation never receives that flag.

**L1 initial-system identity.** The structure actually used for reference generation
(the agent's AIMD trajectory frame 0) is 192 atoms, composition {O:64, H:128}, and a
periodic cell consistent with `system.json` (12.4 Å ± 0.5). Prevents system swapping.

**L2 structure preparation / relaxation.** The reference-generation trajectory starts
from a physically sane structure: no hard close contacts (min pair distance ≥ 1.2 Å),
finite forces, trajectory stable. Running GEO_OPT first is the expert path; the
verifier records `used_geometry_optimization` as a diagnostic (not a hard requirement
on the name). A trajectory that is broken from the start fails L2/L3.

**L3 reference-data authenticity.** Real CP2K outputs: AIMD pos/frc/cell exist with
consistent frame counts (all three follow the trajectory print cadence) and the
per-step `.ener` file is as-dense-or-denser than the pos trajectory; energies finite
and physical (per-atom eV within bounds); forces finite and nonzero; frames distinct
(no duplicated structures). Every labeled training frame spot-checks trace to a real
CP2K output — any AIMD trajectory OR active-learning label run in the workspace
(coordinate match). Fabricated extxyz → FAIL.

**L4 reference sampling quality.** Enough frames (≥ draft min), sampled across a
physical temperature distribution (std > floor, mean within band), structurally
diverse, no pathological frames. A single static geometry is not reference data.

**L5 DeePMD model authenticity.** ≥1 model file, non-empty, loadable by `dp`
(inference on one frame returns finite E/F), type_map [O,H]; if multiple models,
not byte-identical copies.

**L6 genuine iterative improvement.** ≥1 closed loop: model-driven acquisition of new
configurations (e.g. LAMMPS explore + model-deviation screen, or any sound
implementation) → first-principles labeling of the selected configurations (real CP2K
outputs, energies present) → dataset grows (final training frames > initial, extras
not duplicates) → retrained model (≥2 training rounds distinguishable by artifacts).
Method is open; the closed loop is not. Claimed-but-missing new labels → FAIL.

**L7 hidden E/F.** DeepMD inference on `dft-validation.extxyz` vs hidden reference →
Energy RMSE (eV/atom, mean-offset aligned) and Force RMSE (eV/Å). Draft: E < 0.01,
F < 0.06. (Bounds in `reference/thresholds.json`; freeze after expert reruns.)

**L8 hidden 300 K NVT.** Verifier runs its own short NVT with the primary model
(LAMMPS + deepmd pair style; steps from `AI2KIT_NVT_STEPS`): no NaN, no lost atoms,
mean T within [250,350] K. Agent does NOT need to have run NVT itself.

**L9 hidden RDF.** O-O/O-H/H-H RDF from the verifier's NVT trajectory; first-peak
positions within the windows and within `max_peak_shift` of `rdf-reference.json`;
no nonphysical short-range peaks.

Reward: **1.0 iff L0–L9 all pass.** L0–L6 are structural/authenticity gates (hard).
L7–L9 tolerances read from `thresholds.json` (draft; frozen after expert reruns).

### Fixtures (test_outputs.py), per design §二十六

| fixture | expectation |
|---|---|
| oracle: solution/expert smoke run (full closed loop, real model) | PASS |
| alt-valid: oracle repackaged with fewer/differently-named rounds (2–3 rounds, scientifically good) | PASS |
| only `final/manifest.json`, no model | FAIL (L1/L5) |
| corrupt/random `.pb` | FAIL (L5) |
| forged CP2K: labeled training frames trace to no AIMD output | FAIL (L3) |
| only GEO_OPT, no AIMD reference data | FAIL (L3) |
| AIMD present but SCF unconverged / energies nonphysical | FAIL (L3/L4) |
| trained DeePMD but no iterative-improvement loop | FAIL (L6) |
| claims new labels, but the "new" frames are duplicates of training data | FAIL (L6) |
| new CP2K outputs present but never used in retraining | FAIL (L6) |
| model passes L1–L6 but hidden E/F bad | FAIL (L7) |
| E/F ok but hidden NVT crashes / loses atoms | FAIL (L8) |
| NVT stable but RDF nonphysical / peaks shifted | FAIL (L9) |

The genuinely-bad-model fixtures (L7/L8/L9 negatives) require training a bad model,
so they are formal-phase; smoke covers the cheap structural negatives (no model,
corrupt model, forged data, no AIMD, no loop, fake labels) by tampering the oracle
copy. See `VALIDATION.json` G-gates.

---

## 6. task.toml essentials

```toml
schema_version = "1.2"
[task]
name = "benchmark/034-ai2kit-water64-end-to-end-potential"
description = "End-to-end development of a reliable DeePMD water potential (CP2K AIMD -> active learning -> validation) starting from an unrelaxed 64-H2O structure"
execution_backend = "container_simulated_slurm"   # v1; real_remote_scheduler=false
keywords = ["ai2-kit", "deepmd", "cp2k", "active-learning", "water", "end-to-end", "slurm-workflow"]
[verifier]
timeout_sec = 10800.0
[verifier.env]
AI2KIT_HIDDEN_VALIDATION = "/tests/hidden/dft-validation.extxyz"
AI2KIT_HIDDEN_RDF_REFERENCE = "/tests/hidden/rdf-reference.json"
AI2KIT_NVT_STEPS = "5000"
AI2KIT_NVT_TEMPERATURE = "300"
[agent]
timeout_sec = 86400.0
[environment]
os = "linux"
cpus = 16
memory_mb = 32768
storage_mb = 102400
gpus = 1
allow_internet = false
```

---

## 7. solution/expert (container-adapted expert workflow, 4 stages)

The oracle implements the hidden lineage (not its exact schedule) as four stages,
each container-executable via the pseudo-slurm contract:

- `01-geopt/` — CP2K GEO_OPT of the PACKMOL structure (input from
  `reference/expert-trajectory/geopt/`). Rationale: PACKMOL random fills have close
  contacts; AIMD directly is unstable.
- `02-aimd/` — CP2K AIMD (NVT 300 K) from the optimized structure; convert outputs to
  a labeled training set.
- `03-active-learning/` — ai2-kit CLL loop: initial train → LAMMPS explore → model-
  deviation screen → CP2K label → retrain, ≥1 full round.
- `04-validation/` — dp-test, NVT, RDF against the agent's own data.
- Top-level `run.sh` orchestrates the four stages; a manifest writer produces the
  `final/` contract.

Adaptations from HPC: module loads replaced by the container venv (`/opt/ai2kit/bin`
on PATH; no `module load`); Slurm headers rewritten to pseudo-slurm-friendly (keep
`#SBATCH` lines, drop HPC conda-activate paths); LAMMPS plugin path fixed to the
container venv. Small smoke profile (few training steps, short MD) for the harness
proof; full expert schedule for formal runs (env-gated).

---

## 8. Base image (base-env-build/ai2kit/)

`dftworld-base-ai2kit:<version>-cpu`:
- FROM `cp2k/cp2k:latest` (provides cp2k.psmp/popt + GTH basis/potential files).
- install uv; build `/opt/ai2kit` venv (python3.11):
  `ai2-kit==1.1.0`, `oh-my-batch==0.7.6`, `deepmd-kit[lmp]==2.2.11`,
  `tensorflow==2.16.2`, `ase`, `dpdata==0.2.18`, `numpy==1.24.3`, `scipy`,
  `matplotlib`, `pytest==8.3.5`, `pymatgen`, `mdanalysis`.
- ENV: PATH, LAMMPS_PLUGIN_PATH, TF_CPP_MIN_LOG_LEVEL, OMP_NUM_THREADS=1.
- pseudo-slurm installed into `/opt/pseudo-slurm/` + symlinked on PATH.
- NO ai2-kit upstream repo checkout (sdist is clean), NO examples/skills.
- Build-time smoke_test.py loads deepmd + ai2-kit + runs one dp inference.

GPU note: task.toml requests gpus=1 for fairness; v1 image is CPU (matches
matclaw `-cpu`). A CUDA variant is the formal upgrade path — record this in
VALIDATION.json, do not let it block the smoke proof.

---

## 9. Provenance honesty (source.lock.json + VALIDATION.json)

```json
{ "execution_backend": "containerized_simulated_slurm",
  "real_remote_scheduler": false,
  "benchmark_adaptation_of": "/public/home/<site-user>/ai2kit (HPC ai2-kit water64 end-to-end potential workflow)" }
```
Also pin: public `water64.xyz` sha256, expert AIMD raw file sha256,
ai2-kit==1.1.0, oh-my-batch==0.7.6, deepmd-kit==2.2.11, tensorflow==2.16.2,
cp2k image tag, expert metrics, hidden-validation file hashes.
VALIDATION.json gates G0–G16 (design §二十四); honest pass/pending status.
