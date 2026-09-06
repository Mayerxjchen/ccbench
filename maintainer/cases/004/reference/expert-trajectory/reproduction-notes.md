# Reproduction Notes — 64 H2O DeepMD water potential training (expert record)

> **PROVENANCE**
> This file is the restructured reproduction record of a **real HPC ai2-kit workflow**
> that ran at:
>
> - Project root: `/public/home/<site-user>/ai2kit`
> - Work directory: `/public/home/<site-user>/ai2kit/ai2kit`
> - Date of expert run: 2026-06-15
>
> Benchmark **034 (`034-ai2kit-water64-end-to-end-potential`)** is a *controlled,
> containerized adaptation* of that HPC workflow. This subtree is the **HIDDEN
> expert reference / oracle strategy**; it is never copied to `/app` and the agent
> never sees it. The public agent-visible input is ONLY the unrelaxed PACKMOL
> structure (`initial/water64.xyz`); the raw AIMD outputs this record processed
> are now the hidden reference (`aimd/`), and the 190-frame mother set doubles as
> the hidden scientific validation set.
>
> Every number and table below is taken verbatim from the expert record
> `ai2kit/plans.md` (last updated 2026-06-15).

---

## 0. Summary of results

The full active-learning loop was run end to end:

```text
CP2K AIMD raw output
-> cp2k2extxyz.py generates a 190-frame labeled extxyz mother set
-> setup.sh samples 50 frames from the mother set as initial DeepMD data
-> ai2-kit completes 5 rounds of train / explore / screen / label / update
-> iter-005 produces 4 final DeepMD models
-> dp-test succeeds, force RMSE ~ 0.043 eV/A
-> model-deviation and RDF plots generated; RDF recomputed on the filtered config/aimd.xyz
```

Core results:

| Item | Result |
|------|--------|
| AIMD mother set | `config/aimd.xyz`, 190 frames |
| Initial training set | setup samples 50 of the 190 frames |
| Active-learning rounds | 5 completed |
| Final models | `workdir/iter-005/deepmd/model-*/compress.pb` |
| dp-test Energy RMSE | `6.710e-04 eV/atom` |
| dp-test Force RMSE | `4.288e-02 eV/A` |
| dp-test Force R2 | `0.997` |
| RDF O-O first peak | AIMD `280 pm`, MLP `278 pm` |

---

## 1. Data provenance

### 1.1 System and method

| Item | Setting |
|------|---------|
| System | 64 H2O |
| Atoms | 192 |
| Box | 12.4 A |
| DeepMD type_map | `[O, H]` |
| DeepMD rcut | 6.0 A |
| DFT | CP2K, BLYP-D3 / TZV2P-GTH |
| Active learning | ai2-kit CLL |

Box check:

```text
L / 2 = 6.2 A
rcut = 6.0 A
L / 2 > rcut
```

Result: PASS — a 12.4 A box satisfies the minimum-image requirement for DeepMD
`rcut = 6.0 A`.

### 1.2 Raw AIMD files

Raw CP2K AIMD output is preserved in:

```text
ai2kit/data/cp2k-aimd/output/
```

Key files:

| File | Role |
|------|------|
| `water64_aimd-pos-1.xyz` | CP2K AIMD coordinate trajectory |
| `water64_aimd-frc-1.xyz` | CP2K AIMD force trajectory |
| `water64_aimd-1.cell` | box information |
| `water64_aimd-1.ener` | energy and temperature information |

Checked: 214 coordinate frames, 214 force frames. PASS — coordinate and force
frame counts match, safe to enter extxyz conversion.

### 1.3 AIMD conversion script

Script: `ai2kit/tools/cp2k2extxyz.py` — version
`cp2k2extxyz.py 2026-06-14-filter-unique-stable`.

Default behavior:
- delete step 0 (initial frame)
- delete later copies of duplicate steps
- never duplicate frames just to reach a target count

Formal conversion command:

```bash
cd /public/home/<site-user>/ai2kit/ai2kit

python tools/cp2k2extxyz.py \
  --dir data/cp2k-aimd/output \
  --prefix water64_aimd \
  --min-temp 250 \
  --max-temp 350 \
  --output config/aimd.xyz \
  --report data/cp2k-aimd/processed/filter.tsv
```

Conversion result:

```text
214 raw frames
-> delete step 0
-> delete duplicate steps
-> temperature window 250-350 K
-> 190 frames written to config/aimd.xyz
```

Traceable outputs:

```text
ai2kit/config/aimd.xyz
ai2kit/data/cp2k-aimd/processed/filter.tsv
```

Result: COMPLETE — a stable, deduplicated, non-padded AIMD mother set.

---

## 2. The 50-frame / 200-frame misconception (corrected)

### 2.1 Previous wrong understanding

The earlier understanding was:

```text
reference has 200 frames of AIMD
setup samples only 50 frames for training
therefore own config/aimd.xyz also only needs 50 frames
```

This leads to `50-frame mother set -> setup samples 50`, i.e. no real mother-set
sampling, narrowing initial data coverage.

### 2.2 Current correction

Current design:

```text
190-frame stable AIMD mother set -> setup samples 50 -> dp-init-data
```

Consistent with the reference logic:

```text
reference:
200-frame AIMD mother set -> setup samples 50

this project:
190-frame AIMD mother set -> setup samples 50
```

The key point is not to reach exactly 200 frames, but that
`config/aimd.xyz` should be a large, stable, representative AIMD mother set, and
`workflow/setup.sh` does the 50-frame sampling. 190 unique stable frames are
preferred over force-duplicating to 200.

Result: FIXED — the data entry is now consistent with the reference mother-set
sampling logic.

---

## 3. ai2-kit workflow

### 3.1 Project entry

```text
ai2kit/run.sh
```

### 3.2 Configuration entry

```text
ai2kit/config/
```

| Path | Role |
|------|------|
| `config/aimd.xyz` | initial AIMD labeled extxyz mother set |
| `config/deepmd/` | DeepMD training template |
| `config/lammps/` | LAMMPS explore template |
| `config/cp2k/` | CP2K label template |
| `workflow/setup.sh` | generate initial data from `config/aimd.xyz` |
| `workflow/iter-classic-dp-lammps-cp2k.sh` | single active-learning round |
| `workdir/` | run artifacts |

### 3.3 Setup phase

Run:

```bash
cd /public/home/<site-user>/ai2kit/ai2kit
bash workflow/setup.sh
```

Produces:

```text
workdir/dp-init-data/
workdir/lammps-data/
workdir/setup.done
```

Logic:

```text
config/aimd.xyz
-> sample 50
-> workdir/dp-init-data

config/aimd.xyz
-> sample 2
-> workdir/lammps-data
```

Result: COMPLETE — input `config/aimd.xyz` (190 frames); outputs
`workdir/dp-init-data/O64H128/set.000/coord.npy`, `workdir/lammps-data/000.data`,
`workdir/lammps-data/001.data`, marker `workdir/setup.done`.

### 3.4 Active-learning parameters

| Parameter | Value |
|-----------|-------|
| `MODEL_NUM` | 4 |
| `TYPE_MAP` | `[O,H]` |
| `MODEL_DEVI_COND` | `--lo 0.2 --hi 0.4` |
| `USE_BAD_CONFS` | 0 |

Model-deviation screening:

```text
max_devi_f < 0.2        -> good
0.2 <= max_devi_f < 0.4 -> decent, send to CP2K label
max_devi_f >= 0.4       -> poor
```

Iteration schedule:

| Iter | TRAIN_STEPS | MD_STEPS | temps | SAMPLE_FREQ | MAX_LABEL |
|------|-------------|----------|-------|-------------|-----------|
| 001 | 100k | 1,000 | 330/430/530 K | 10 | 20 |
| 002 | 100k | 4,000 | 330/430/530 K | 100 | 20 |
| 003 | 400k | 100,000 | 330/430/530/630 K | 100 | 50 |
| 004 | 400k | 100,000 | 330/430/530/630 K | 100 | 50 |
| 005 | 400k | 100,000 | 330/430/530/630 K | 100 | 50 |

Result: COMPLETE — entry `bash run.sh`; rounds iter-001 through iter-005; the
`train -> explore -> screen -> label -> update` main loop ran through.

---

## 4. Active-learning results

### 4.1 Completion markers

All 5 rounds completed:

```text
workdir/iter-001/iter.done
workdir/iter-002/iter.done
workdir/iter-003/iter.done
workdir/iter-004/iter.done
workdir/iter-005/iter.done
```

Result: 5 rounds complete; final round iter-005; final model directory
`workdir/iter-005/deepmd/`; the loop did not stop mid-way.

### 4.2 Screening statistics

Raw files:

```text
workdir/iter-001/screening/stats.tsv
workdir/iter-002/screening/stats.tsv
workdir/iter-003/screening/stats.tsv
workdir/iter-004/screening/stats.tsv
workdir/iter-005/screening/stats.tsv
```

Summary:

| Iter | Total | Good | Decent | Poor | Good% | Decent% | Poor% | Outlier |
|------|-------|------|--------|------|-------|---------|-------|---------|
| 001 | 546 | 530 | 15 | 1 | 97.07% | 2.75% | 0.18% | 0 |
| 002 | 186 | 184 | 2 | 0 | 98.92% | 1.08% | 0.00% | 0 |
| 003 | 7,928 | 2,152 | 320 | 5,456 | 27.14% | 4.04% | 68.82% | 4,865 |
| 004 | 7,928 | 7,853 | 63 | 12 | 99.05% | 0.79% | 0.15% | 2 |
| 005 | 7,928 | 7,901 | 26 | 1 | 99.66% | 0.33% | 0.01% | 0 |

Interpretation:

```text
iter-001/002:
  330-530 K short exploration already fairly stable; few decent.

iter-003:
  100000 steps + 630 K long exploration exposes model boundary; poor/outlier up.

iter-004/005:
  after new CP2K labels join, fast convergence; good > 99%, poor near 0.
```

Result: clear convergence trend — iter-003 long-range exploration at 630 K
exposes many model boundaries; iter-004 good rises to 99.05%; iter-005 good rises
to 99.66%, poor drops to 0.01%. The active learning effectively supplements model
boundary regions.

### 4.3 Model-deviation plot

File: `ai2kit/test/model-devi/output/model-devi.png`. Used to track the
good/decent/poor ratio across the 5 rounds. (Plot artifact referenced by the
expert; not reproduced here.)

---

## 5. Final models

Final models live at:

```text
ai2kit/workdir/iter-005/deepmd/model-0/compress.pb
ai2kit/workdir/iter-005/deepmd/model-1/compress.pb
ai2kit/workdir/iter-005/deepmd/model-2/compress.pb
ai2kit/workdir/iter-005/deepmd/model-3/compress.pb
```

These 4 models are jointly used for:

```text
model deviation
LAMMPS explore
NVT stability validation
RDF validation
```

Result: COMPLETE — 4 models; iter-005 products form the final DP potential
committee.

---

## 6. dp-test validation

### 6.1 Script

`ai2kit/test/dp-test/run_dp_test.sh`. Fixed: `module` is unavailable in the
Slurm job, so `source /etc/profile.d/modules.sh` was added.

### 6.2 Test data

```text
config/aimd.xyz: 190 frames
setup initial training frames: 50
held-out test frames: 140
```

Test data location:

```text
ai2kit/test/dp-test/test-data/O64H128/
```

Result: COMPLETE — input `config/aimd.xyz` (190 frames); training frames 50;
test frames 140; the test set is disjoint from the initial training set and is
usable for a held-out dp-test.

### 6.3 Run record

```text
JobID: 2882277
State: COMPLETED
ExitCode: 0:0
Elapsed: 00:02:19
```

### 6.4 Result plot

`ai2kit/test/dp-test/output/dp-test.png` (parity plot).

### 6.5 Metrics

Summary in plot:

```text
Energy RMSE: 6.710e-04 eV/atom
Energy MAE : 5.560e-04 eV/atom
Energy R2  : 0.954

Force RMSE : 4.288e-02 eV/A
Force MAE  : 3.331e-02 eV/A
Force R2   : 0.997
```

Single-model log range:

```text
Energy RMSE/Natoms: 6.41e-04 - 7.19e-04 eV/atom
Force RMSE:         4.19e-02 - 4.37e-02 eV/A
```

Judgment: force prediction is very good, Force R2 = 0.997; energy error ~ 0.67
meV/atom, acceptable for an initial water potential for further validation.

Result: PASS — Energy RMSE `6.710e-04 eV/atom`, Force RMSE `4.288e-02 eV/A`,
Force R2 `0.997`.

---

## 7. NVT and RDF validation

dp-test validates energy/force error. NVT/RDF validate whether the model, when it
drives MD itself, maintains a reasonable liquid-water structure.

### 7.1 Why RDF needs NVT

```text
dp test:
  checks E/F accuracy on fixed test frames.

NVT:
  uses the final DP model to independently generate a 300 K liquid-water trajectory.

RDF:
  computes the O-O / O-H / H-H distance distributions from the NVT trajectory.
```

If the model is accurate only on test frames but its structure drifts when it
runs MD itself, the RDF exposes that. The RDF is not computed directly from the
training set to avoid only verifying memorization.

### 7.2 RDF plots

Plots: `ai2kit/test/rdf/output/compare_rdf.png`,
`compare_rdf_OO.png`, `compare_rdf_OH.png`, `compare_rdf_HH.png`.

Check key points:

```text
O-O first peak should be near 2.7-2.9 A
O-H / H-H peak positions should align with AIMD
MLP curves should show no obvious nonphysical peaks or long-range drift
```

Recomputed: AIMD data source `config/aimd.xyz` (190 frames); MLP source
`test/nvt/output/dump.lammpstrj`; MLP equilibration default skips the first 100
frames, leaving 701 MLP frames for RDF; box read from the trajectories, both
`[12.4, 12.4, 12.4]` A.

### 7.3 RDF peak positions

| Pair | AIMD first peak | MLP first peak | Difference | Judgment |
|------|-----------------|----------------|------------|----------|
| O-O | `280 pm` | `278 pm` | `3 pm` | aligned well |
| O-H | `98 pm` | `98 pm` | `0 pm` | aligned well |
| H-H | `158 pm` | `158 pm` | `0 pm` | aligned well |

Result: PASS — run as `MLP_SKIP_FRAMES=100 bash test/rdf/compare_rdf.sh`
(2026-06-15 13:48). Peaks are essentially aligned, so the final DP model
preserves reasonable liquid-water local structure under a 300 K NVT.

### 7.4 RDF script fix record

Previous issues:

```text
AIMD RDF used the raw trajectory data/cp2k-aimd/output/water64_aimd-pos-1.xyz
script hard-coded CELL = [12.42, 12.42, 12.42]
MLP NVT trajectory did not explicitly skip equilibration
```

After fixes:

```text
AIMD RDF uses config/aimd.xyz, the filtered labeled extxyz mother set
box read from the trajectory, no hard-coded 12.42 A
MLP_SKIP_FRAMES defaults to 100, adjustable via environment variable
embedded Python reads paths from Bash environment variables
```

Result: FIXED — `bash -n test/rdf/compare_rdf.sh` passes; `--plot-only` mode
passes; `all` mode recomputes RDF successfully; conditions are fairer than the
old version for comparison with the reference.

---

## 8. File index

### 8.1 Inputs and intermediates

```text
ai2kit/data/cp2k-aimd/output/
ai2kit/data/cp2k-aimd/processed/filter.tsv
ai2kit/config/aimd.xyz
ai2kit/workdir/dp-init-data/
ai2kit/workdir/lammps-data/
```

### 8.2 Active-learning results

```text
ai2kit/workdir/iter-*/screening/stats.tsv
ai2kit/workdir/iter-*/new-dataset/
ai2kit/workdir/iter-005/deepmd/model-*/compress.pb
```

### 8.3 Validation results

```text
ai2kit/test/dp-test/output/
ai2kit/test/model-devi/output/
ai2kit/test/rdf/output/
ai2kit/test/nvt/output/
```

---

## 9. Resolved-problems log

| Problem | Cause | Fix |
|---------|-------|-----|
| PACKMOL structure is unstable directly in AIMD | random packing has close contacts | GEO_OPT first |
| CP2K AIMD input `&ENERGY` error | `&MOTION/&PRINT/&ENERGY` invalid | removed the section |
| 50-frame mother set too narrow | `config/aimd.xyz` trimmed to 50 then sampled 50 | switched to a 190-frame mother set |
| duplicate frames in AIMD conversion | raw output has duplicate steps, script did not dedupe | default deletes later copies of duplicate steps |
| step 0 mixed into mother set | initial frame is not representative of stable AIMD sampling | default deletes step 0 |
| `module` missing in dp-test | Slurm did not initialize modules | added `source /etc/profile.d/modules.sh` |
| unfair RDF comparison | AIMD used the raw trajectory and box was hard-coded 12.42 A | use `config/aimd.xyz`, read box from trajectory, MLP skips first 100 frames |
| LAMMPS 630 K possible lost atoms | high-temperature long-range exploration touches model boundary | converged in later rounds through new labeled data |

---

## 10. Current status and next steps

Completed:

```text
[x] CP2K AIMD raw data preserved
[x] 190-frame AIMD mother set generated
[x] 50/200-frame misconception corrected
[x] ai2-kit 5-round active learning
[x] iter-005 final models generated
[x] dp-test succeeded
[x] model-deviation plot generated
[x] RDF plots generated and recomputed on filtered AIMD data
```

Suggested next steps (expert notes):

```text
[ ] check NVT 300 K long-range stability log
[ ] sync dp-test, model-deviation, RDF results into the README
[ ] archive iter-005 models and key figures
[ ] from this project template, summarize the steps a future ai2-kit agent needs to automate
```

---

*End of reproduction record. All numbers above are the expert HPC results; the
containerized 034 adaptation may differ in schedule/profile but the reference
targets are these metrics.*
