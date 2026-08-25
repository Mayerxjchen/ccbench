# 042 CONTRACT — end-to-end GO–water DPMP potential development

Case: `042-go-water-dpmp`
Formal execution backend: **real_hpc_controller**. PAgent runs in an isolated
sandbox as the control layer and discovers/uses a real remote Slurm cluster.
The containerized pseudo-Slurm is a developer test backend only and is not valid
evidence for a formal pilot or formal ablation run.

The agent starts from the paper's **published initial structures** and system
definitions (no labeled training data, no trained model) and must reproduce the
full development cycle: generate CP2K revPBE-D3 first-principles labels, train a
DPMP potential (deepmd-jax), iteratively improve it through active learning, and
demonstrate static + dynamic + physical reliability. Methods are open; only the
scientific stages are fixed (see `instruction.md`).

This file is the single source of truth for paths, names, and the output
contract. All builders (base image, solution, verifier) MUST agree with it.

---

## 1. Directory layout (repo)

```
042-go-water-dpmp/
├── CONTRACT.md
├── Dockerfile                  # FROM dftworld-base-deepmd-jax:<tag> ; COPY public/ /app/
├── instruction.md              # end-to-end prompt: stages fixed, methods open
├── task.toml                   # hpc_controller
├── case-design.yaml            # builder design (hidden from agent)
├── case-requirements.yaml      # category requirements (hidden)
├── verifier-plan.yaml          # MLP layers + fixtures (hidden)
├── public/                     # the ONLY thing the agent sees (via COPY public/ /app/)
│   ├── README.md
│   ├── system.json             # 5 interfaces, compositions, cells, oxidation levels
│   ├── reference-method.json   # CP2K revPBE-D3 fingerprint + ai2-kit stages
│   ├── dpmp-config/train.py    # published deepmd-jax DPMP training recipe
│   └── structures/             # published initial supercells (DeepMD raw)
│       ├── air-water/          #   O752 H1504, 29.520 x 25.566 x 70.000 A
│       ├── graphene-water/     #   C576 O752 H1504
│       ├── graphene-O12/       #   C576 O824 H1536 (12.5%, 32 OH + 40 epoxide)
│       ├── graphene-O25/       #   C576 O896 H1576 (25%, 72 OH + 72 epoxide)
│       └── graphene-O50/       #   C576 O1040 H1648 (50%, 144 OH + 144 epoxide)
├── reference/                  # hidden from agent
│   ├── expert-trajectory/      # hidden pipeline lineage (provenance), bounded real run
│   ├── hidden-validation/      # held-out frames + density reference + manifest
│   │   └── generator/          # generate_hidden_validation.py (no HPC needed)
│   ├── reference.json          # expert metrics (machine-readable)
│   ├── thresholds.json         # draft thresholds (freeze after n_runs>=2 reruns)
│   ├── compute-runtime.lock.json
│   └── source.lock.json        # provenance + hashes + backend declaration
├── solution/
│   └── expert/                 # container-adapted runnable expert workflow
│       ├── 01-aimd/            #   stage 1: CP2K AIMD reference labels
│       ├── 02-train/           #   stage 2: DPMP training (deepmd-jax)
│       ├── 03-active-learning/ #   stage 3: iterative improvement (ai2-kit style)
│       ├── 04-validation/      #   stage 4: scientific validation (E/F, NVT, density)
│       ├── run.sh              #   orchestrator
│       └── README.md
├── tests/                      # hidden evaluator (staged into /tests at verify time)
│   ├── test.sh
│   ├── verifier.py             # MLP-V0..V6 outcome-based verifier
│   ├── test_outputs.py         # oracle + negative/alt-valid fixtures
│   ├── hidden/                 # REAL hidden set (committed from reference/hidden-validation)
│   └── fixtures/               # synthetic fixtures for local smoke
│       ├── positive/
│       ├── negative/
│       └── alternative-valid/
├── profiles/
│   ├── resource.yaml           # batch/GPU requirements
│   ├── platform.yaml           # HPC platform contract
│   ├── smoke.yaml
│   └── formal.yaml
├── tools/                      # leak_audit.py, container_smoke_c.sh, etc.
├── evidence/                   # retention-policy.yaml, manifest.json, sealed runs
├── VALIDATION.json             # G0–G12 gates (honest pass/pending)
└── benchmark_valid.json        # generated; only check_release.py writes true
```

Base image build: `base-env-build/deepmd-jax/` (Dockerfile + pseudo-slurm/ +
build.sh entry).

---

## 2. Public data (agent-visible)

Seeded under `/app` by `COPY public/ /app/`; the instruction is supplied
separately by the harness. No labeled training frame, no trained model, no
threshold, no expert artifact, no cluster recipe is agent-visible.

| path | role |
|------|------|
| `system.json` | the five target interfaces, compositions, cells, oxidation levels (SI §1.1–1.2) |
| `reference-method.json` | CP2K revPBE-D3 labeling fingerprint + ai2-kit active-learning stages + final DPMP training recipe summary |
| `dpmp-config/train.py` | the published deepmd-jax DPMP training configuration |
| `structures/<iface>/{box,coord,type}.raw` | published MLMD supercell initial structures (DeepMD raw) |

The agent is told the ML potential family is DPMP (deepmd-jax), that CP2K
revPBE-D3 is the reference method, and that ai2-kit-style active learning must
materially participate. It must generate every labeled training datum itself and
retain data provenance. The sandbox exposes a discoverable remote-HPC capability
without public connection instructions; all cluster facts and scientific methods
remain the agent's to discover.

---

## 3. Runtime software (inside the container, provided by dftworld-base-deepmd-jax)

- Base: `dftworld-base-ai2kit` (provides CP2K, ai2-kit, oh-my-batch, deepmd-kit,
  LAMMPS, venv) + deepmd-jax and `jax[cuda]` for the DPMP (JAX) path.
- Binaries the agent may use: `cp2k` / `cp2k.psmp`, `ai2-kit`, `dp`, `lmp`,
  `jax-md` (via python), deepmd-jax `train`/`test`/`evaluate` python APIs,
  `python` (ase, dpdata, numpy, scipy, jax, deepmd_jax, matplotlib).
- pseudo-slurm on PATH: `sbatch`, `squeue`, `sacct`, `scancel`, `sinfo`
  (same contract as 034 §3: real job lifecycle over a JSONL job DB, NOT
  `sbatch = bash`; omb exit-code injection must be preserved; states
  PENDING/RUNNING/COMPLETED/FAILED/CANCELLED).
- `~/.pseudo_slurm/` is the scheduler state dir; `PSEUDO_SLURM_DIR` may override.

---

## 4. Agent output contract (`/app/final/`)

The agent decides internal layout EXCEPT these fixed names:

```
final/
├── manifest.json          # machine-readable summary
├── workflow/              # reproducible workflow (scripts + logs)
├── models/                # final trained DPMP model file(s)
├── provenance/            # data provenance (what went into training, incl. all
│                          #   first-principles data generated during the project)
├── validation/            # optional: agent's own dp-test/nvt/density artifacts
└── report.md              # concise scientific report
```

`manifest.json` schema:

```json
{
  "model_family": "DPMP",
  "framework": "deepmd-jax",
  "structure_origin": {
    "source": "public/structures/<iface> (published initial supercell)",
    "used_interfaces": ["air-water", "graphene-water", "graphene-O12"],
    "structure_preparation_sha256": "64 lowercase hex characters"
  },
  "reference_labeling": {
    "engine": "CP2K",
    "functional": "revPBE-D3",
    "total_labeled_frames": 0,
    "frame_count_per_source": {"interface_or_iteration": "count"}
  },
  "model_files": ["models/final/model.pkl"],
  "workflow_root": "workflow",
  "iterative_improvement": {
    "rounds": 0,
    "explorer": "jax-md|lammps",
    "labeling_engine": "CP2K",
    "final_training_frames": 0
  },
  "validation_artifacts": ["validation/..."],
  "environment_manifest": "deepmd-jax X, jax X, cp2k X, ai2-kit X",
  "status": "completed"
}
```

`model_files` are workspace-relative paths to the trained DPMP model (the
deepmd-jax pickle, or an exported frozen graph). At least one must exist, be
loadable by deepmd-jax, and be referenced. The verifier NEVER requires any
particular directory names beyond the `final/` skeleton above. The verifier does
NOT trust the manifest's prose; every claim is cross-checked against workspace
artifacts (see §5 V0–V6).

---

## 5. Hidden validation set + verifier design (tests/, outcome-based)

### Hidden set (no HPC required to hold)

- `reference/hidden-validation/hidden-frames/` — held-out frame subset drawn
  from the published 14140-frame labeled dataset (energy + forces, DeepMD raw).
  Generator in `reference/hidden-validation/generator/`. Frames are removed from
  any candidate-visible material and overlap-checked against the candidate
  submission (a candidate that somehow used a hidden frame fails).
- `reference/hidden-validation/density-reference.json` — water number-density
  profile across the interface (z-direction) with peak positions and depletion
  width, computed from the published reference data.
- Committed copies live in `tests/hidden/`; staged into `/tests` ONLY by
  `eval.verify()`; the agent never sees them. Paths injected via task.toml
  `[verifier.env]`.

### Verifier (tests/verifier.py, `verify(submission, profile) -> dict`)

`test.sh`: run the verifier and write `echo 1|0 > /logs/verifier/reward.txt`.

The verifier locates the submission at `$AI2KIT_042_SUBMISSION` (default
`/app`); dev smoke runs point it at a fixture dir. It never inspects anything
outside the workspace except the staged hidden files under `/tests/hidden/`.

**V0 system/submission identity.** `final/` skeleton present; model referenced;
type_map [O,H,C] consistent with `system.json`; submission in the expected root.

**V1 data/label provenance.** Every labeled training frame in the workspace
traces to a real CP2K AIMD/label output (coordinate match); energies finite and
physical; forces finite and nonzero; no fabricated or duplicated training
frames; per-datum provenance recorded.

**V2 model authenticity.** ≥1 model file, non-empty, loadable by deepmd-jax
(inference on one frame returns finite E/F); type_map [O,H,C]; if multiple
models, not byte-identical copies.

**V3 iterative workflow integrity.** ≥1 closed active-learning loop:
model-driven acquisition of new configurations (explore + model-deviation
screen, any sound implementation) → CP2K labeling of selected configurations
(real CP2K outputs, energies present) → dataset grows (final training frames >
initial, extras not duplicates) → retrained model (≥2 distinguishable training
rounds). Claimed-but-missing new labels → FAIL.

**V4 hidden static accuracy.** deepmd-jax inference on the hidden held-out
frames vs the hidden DFT labels → Energy RMSE (eV/atom, mean-offset aligned) and
Force RMSE (eV/Å) within frozen thresholds.

**V5 hidden dynamic stability.** Verifier runs a short 300 K NVT with the
primary model: no NaN, no lost atoms, mean T within [250, 350] K. Agent does NOT
need to have run NVT itself.

**V6 hidden physical observable.** Water number-density profile across the
interface from the verifier's NVT (or a candidate NVT trajectory with recorded
settings): first-layer peak position within a frozen window and depletion width
within tolerance of `density-reference.json`; no unphysical layering artifacts.

Reward: **1.0 iff V0–V6 all pass.** V0–V3 are structural/authenticity gates
(hard). V4–V6 tolerances read from `thresholds.json` (draft; frozen after
n_runs>=2 expert reruns).

### Fixtures (test_outputs.py)

| fixture | expectation |
|---|---|
| oracle: solution/expert smoke run (real closed loop, real model) | PASS |
| alt-valid: oracle repackaged with a different valid schedule (2–3 rounds, scientifically good) | PASS |
| only `final/manifest.json`, no model | FAIL (V0/V2) |
| corrupt/random model pickle | FAIL (V2) |
| forged CP2K: labeled frames trace to no CP2K output | FAIL (V1) |
| only AIMD labels, no training / no model | FAIL (V0/V2) |
| CP2K present but energies nonphysical | FAIL (V1) |
| trained DPMP but no iterative-improvement loop | FAIL (V3) |
| claims new labels, but the "new" frames duplicate training data | FAIL (V3) |
| new CP2K outputs present but never used in retraining | FAIL (V3) |
| model passes V0–V3 but hidden E/F bad | FAIL (V4) |
| E/F ok but hidden NVT crashes / loses atoms | FAIL (V5) |
| NVT stable but density profile nonphysical / peaks shifted | FAIL (V6) |

The genuinely-bad-model negatives (V4/V5/V6) are formal-phase; smoke covers the
cheap structural negatives (no model, corrupt model, forged data, no loop, fake
labels) by tampering the oracle copy.

---

## 6. task.toml essentials

```toml
schema_version = "1.2"
[execution]
class = "hpc_controller"
[candidate]
image = "dftworld-base-deepmd-jax"
submission_root = "final"
[hpc]
contract_version = "hpc-execution/v1"
required_capabilities = ["batch_jobs", "gpu", "artifact_fetch"]
[task]
name = "benchmark/042-go-water-dpmp"
description = "End-to-end development of the GO-water DPMP potential (CP2K revPBE-D3 AIMD -> active learning -> validation) from the published initial structures"
keywords = ["dpmp", "deepmd-jax", "cp2k", "active-learning", "graphene-oxide", "water", "end-to-end", "slurm-workflow"]
[verifier]
timeout_sec = 10800.0
[verifier.env]
AI2KIT_042_HIDDEN_FRAMES = "/tests/hidden/hidden-frames"
AI2KIT_042_DENSITY_REFERENCE = "/tests/hidden/density-reference.json"
AI2KIT_042_NVT_STEPS = "5000"
AI2KIT_042_NVT_TEMPERATURE = "300"
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

The oracle implements the hidden lineage as four stages, each
container-executable via the pseudo-slurm contract:

- `01-aimd/` — CP2K AIMD (revPBE-D3, NVT 300 K) on one or more supplied
  initial structures; convert outputs to a labeled DeepMD raw training set.
- `02-train/` — initial DPMP training with deepmd-jax (`dpmp-config/train.py`
  adapted to generated data; short schedule in smoke, longer in formal).
- `03-active-learning/` — ai2-kit-style CLL loop: explore (jax-md or lammps) →
  model-deviation screen → CP2K label → retrain, ≥1 full round.
- `04-validation/` — dp-test-style E/F accuracy, NVT stability, density profile
  against the agent's own data.
- Top-level `run.sh` orchestrates the four stages; a manifest writer produces
  the `final/` contract.

Adaptations from HPC: module loads replaced by the container venv; Slurm
headers rewritten to pseudo-slurm-friendly (keep `#SBATCH`, drop HPC
conda-activate paths); deepmd-jax used instead of tensorflow deepmd. Small smoke
profile (few training steps, short MD) for the harness proof; full expert
schedule for formal runs (env-gated).

---

## 8. Base image (base-env-build/deepmd-jax/)

`dftworld-base-deepmd-jax:<version>-cpu` (GPU variant is the formal upgrade path):

- FROM `dftworld-base-ai2kit` (brings CP2K, ai2-kit, oh-my-batch, deepmd-kit,
  LAMMPS, GTH data).
- Add to the venv: `deepmd-jax`, `jax`, `jax-md`, `dm-tree`.
- ENV: PATH, JAX_ENABLE_X64, OMP_NUM_THREADS, CUDA paths for the GPU variant.
- pseudo-slurm installed into `/opt/pseudo-slurm/` + symlinked on PATH.
- Build-time smoke: load deepmd_jax, run one DPMP inference.

GPU note: task.toml requests gpus=1 for fairness; the CPU image is the first
landing. A CUDA variant is the formal upgrade path — record this in
VALIDATION.json, do not let it block the smoke proof.

---

## 9. Provenance honesty (source.lock.json + VALIDATION.json)

```json
{ "execution_backend": "containerized_simulated_slurm",
  "real_remote_scheduler": false,
  "benchmark_adaptation_of": "GO-water DPMP paper workflow (DOI 10.1021/acs.jpclett.5c03713; dataset 10.6084/m9.figshare.30472487 v2)" }
```
Also pin: public structure hashes, hidden-frames manifest hashes, deepmd-jax/jax
versions, cp2k image tag, expert metrics, hidden-validation file hashes.
VALIDATION.json gates G0–G12; honest pass/pending status.
