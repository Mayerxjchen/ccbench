# solution/expert — 034 oracle expert workflow (container-adapted)

A runnable oracle for benchmark **034-ai2kit-water64-end-to-end-potential**:
an end-to-end DeePMD water-potential development cycle (GEO_OPT -> CP2K AIMD ->
ai2-kit active learning -> validation -> `final/` output contract), adapted from
the hidden HPC expert trajectory so it runs inside the `dftworld-base-ai2kit`
container using the pseudo-slurm scheduler.

## Tree

```
solution/expert/
├── env.sh               # container environment (replaces module load / conda)
├── wait_job.sh          # poll pseudo-slurm (sacct) for a job's terminal state
├── manifest_writer.py   # writes final/manifest.json + final/report.md
├── run.sh               # orchestrator: stage 01 -> 04, then builds final/
├── 01-geopt/            # CP2K GEO_OPT of the PACKMOL structure
├── 02-aimd/             # CP2K AIMD (NVT 300 K) -> labeled mother set (aimd.xyz)
├── 03-active-learning/  # ai2-kit closed loop: train -> explore -> screen -> label
└── 04-validation/       # dp-test / NVT 300 K / RDF (AIMD vs MLP)
```

## How the HPC artifacts were container-adapted

| HPC artifact | Container adaptation |
|---|---|
| `module load cp2k/2024.3`, `module load anaconda...` | dropped; every job script `source "$EXPERT_DIR/env.sh"` (venv at `/opt/ai2kit/bin` on PATH) |
| `conda activate /public/.../deepmd/...` | dropped; PATH resolution (`command -v ai2-kit`, `command -v omb`, `dp`, `lmp`, `cp2k.psmp`) |
| `AI2_KIT=/public/.../ai2-kit`, `OMB=.../omb` | `command -v ai2-kit` / `command -v omb` (env.sh puts them on PATH) |
| `plugin load /public/.../libdeepmd_lmp.so` | `plugin load @PLUGIN_PATH@` substituted at template-copy time with the venv's `libdeepmd_lmp.so` (located defensively by env.sh; falls back to `LAMMPS_PLUGIN_PATH`) |
| Slurm `#SBATCH` lines | kept verbatim; pseudo-slurm honours them (`--partition=cpu`, small ntasks for the 16-CPU container) |
| `@SEED@/@TRAIN_STEPS@/@DECAY_STEPS@/@DP_DATASET@` | unchanged omb combo substitution (`json-item` file set for `DP_DATASET`); `@NUMB_TEST@/@DISP_FREQ@/@SAVE_FREQ@` added for env-gating |
| HPC absolute job dirs (`/public/home/<site-user>/...`) | derived from `AI2KIT_034_WORKSPACE` (default `/app`) |

## Profiles (env-gated by `env.sh`)

`AI2KIT_PROFILE` defaults to `paper`; `AI2KIT_SMOKE=1` is an alias for
`AI2KIT_PROFILE=smoke`. Every schedule knob can be individually overridden.

| knob | smoke | paper |
|---|---|---|
| GEO_OPT MAX_ITER | 30 | 200 |
| AIMD steps / print-each | 100 / 5 | 5000 / 25 |
| AIMD T-window | 200-400 K | 250-350 K |
| train steps / decay | 200 / 50 | 40000 / 1000 |
| committee size (MODEL_NUM) | 2 | 4 |
| AL rounds | 1 | 2 |
| MD steps / temps | 100 / "330 430" | 4000 / "330 430 530" |
| model-devi band | `--lo 0.001 --hi 5.0` (guarantees labels) | `--lo 0.2 --hi 0.4` |
| NVT steps | 200 | 5000 |
| setup train sample | 8 | 50 |

Smoke proves the full closed loop (GEO_OPT -> AIMD -> 1 AL round with real CP2K
labels -> retrain -> dp-test/NVT/RDF -> `final/`) in tens of minutes. Paper is
the scientific schedule scaled to a 16-core CPU container.

## Usage (container)

```bash
# from a directory that can reach solution/expert:
bash /path/to/solution/expert/run.sh                 # paper profile
AI2KIT_PROFILE=smoke bash /path/to/solution/expert/run.sh   # harness proof
```

The orchestrator runs stages in order, waits on each marker, then writes the
`final/` output contract into `$AI2KIT_034_WORKSPACE/final` (default `/app/final`).

## final/ contract (CONTRACT §4)

```
final/
├── manifest.json      # schema: model_family, model_files, workflow_root,
│                      #   training_data_provenance, validation_artifacts,
│                      #   environment_manifest, status, profile, metrics
├── models/final/      # compress.pb (primary) + model-N-compress.pb (committee)
├── workflow/          # self-contained copy of the 4 stage dirs + run.sh/env.sh
├── provenance/        # provenance.json
├── validation/        # dp-test / nvt / rdf artifacts
└── report.md          # concise scientific report
```

`model_files` in `manifest.json` is `["models/final/compress.pb"]` (relative to
`final/`, per the CONTRACT example); a copy is also placed at the workspace
root `models/final/compress.pb` so paths resolve against either base.

## Stage markers (idempotency)

`geopt.done`, `aimd.done`, `al.done`, `validation.done`, plus per-substep
`setup.done`/`train.done`/`lammps.done`/`cp2k.done`/`screening.done`/`iter.done`
and per-job `*.exitcode` produced by oh-my-batch.

## Things not verifiable without a running container

- Exact `ai2-kit tool ...` CLI flag behaviour (assumed identical to the HPC
  record; the workflow calls are byte-compatible with the reference).
- `omb combo/batch/job` template rendering of the added `@NUMB_TEST@` etc.
- pseudo-slurm + omb interplay for the concurrency settings.
- `dp freeze/compress` runtime and CP2K timings on the actual image.
