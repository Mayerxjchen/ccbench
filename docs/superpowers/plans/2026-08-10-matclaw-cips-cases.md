# MatClaw CIPS Cases 031–033 Implementation Plan

**Goal:** Build and scientifically validate the three pinned MatClaw CIPS cases.

**Architecture:** One locked DeePMD/LAMMPS image supplies the common physics.
Each case exposes only public inputs, emits raw trajectories plus a strict result
contract, and is evaluated by an independent recomputation path. Smoke and paper
profiles are separate; formal validity is derived from evidence, never declared
manually.

**Tech stack:** Docker, DeePMD-kit, TensorFlow, LAMMPS, ASE, NumPy/SciPy,
pymatgen, dpdata, pytest, Bash.

---

### Task 1: Add red repository contracts

**Files:**
- Create: `tests/test_matclaw_case_contracts.py`

Add failing tests for exact case names, common directory layout, public-only
Docker copy, offline task metadata, two profiles, source-lock identity, and false
validation state before scientific evidence exists. Run the focused test and
confirm it fails because the cases/image do not exist.

### Task 2: Build and verify the shared image

**Files:**
- Create: `base-env-build/matclaw-cips/Dockerfile`
- Create: `base-env-build/matclaw-cips/smoke_test.py`
- Modify: `base-env-build/build.sh`
- Modify: `README.md`

Pin the compatible runtime and install LAMMPS with DeePMD support. Copy and hash
the recovered model/type map. Extend the build resolver. Build the image and run
the six image gates, including finite inference and short CPU MD. Record the
image digest and exact software versions.

### Task 3: Scaffold the three isolated cases

**Files:**
- Create: `031-matclaw-cips-active-distillation/**`
- Create: `032-matclaw-cips-curie-temperature/**`
- Create: `033-matclaw-cips-domain-wall-search/**`

Create public inputs from locked sources, minimal Dockerfiles, instructions that
do not disclose answer values, `task.toml`, source locks, original references,
and false-by-default validation summaries. Make the common contract tests pass.

### Task 4: Implement 032 with TDD

**Files:**
- Create: `032-*/public/run_curie.py`
- Create: `032-*/reference/generate_reference.py`
- Create: `032-*/solution/solve.sh`
- Create: `032-*/tests/test_outputs.py`
- Create: `032-*/tests/fixtures/**`

First encode failing unit tests for supercell identity, trajectory-derived order
parameter, temperature coverage, convergence, independent Tc estimation, and
forged/missing outputs. Implement smoke and paper workflows. Pass unit tests,
Docker oracle, negative fixtures, and one alternative grid.

### Task 5: Run and freeze the 032 paper reference

Run the paper protocol at least twice with declared seeds. Write raw run
manifests, regenerated reference, reproducibility statistics, and the measured
comparison to `261.3 ± 10.0 K`. Set only evidence-backed G0–G12 fields.

### Task 6: Implement 031 with TDD

**Files:**
- Create: `031-*/public/run_distillation.py`
- Create: `031-*/reference/generate_reference.py`
- Create: `031-*/solution/solve.sh`
- Create: `031-*/tests/test_outputs.py`
- Create: `031-*/tests/fixtures/**`

Encode failing tests for real student models, disjoint train/test configuration
hashes, teacher-bound labels, committee deviation, dataset growth, retraining,
diversity, and stop logic. Implement smoke and paper profiles, then pass oracle,
negative, and alternative-valid runs.

### Task 7: Run and freeze the 031 paper reference

Run the paper protocol, compare force MAE, iterations and frame count with the
official Task 1b result, characterize variability, and update validation only
from the generated evidence.

### Task 8: Implement 033 with TDD

**Files:**
- Create: `033-*/public/run_search.py`
- Create: `033-*/public/field_calculator.py`
- Create: `033-*/reference/generate_reference.py`
- Create: `033-*/solution/solve.sh`
- Create: `033-*/tests/test_outputs.py`
- Create: `033-*/tests/fixtures/**`

Encode failing tests for the start point/domain, two-job iteration cap, adaptive
history, exact recovered field physics, trajectory-derived flips, domino fit and
sequential propagation. Implement smoke/paper search and evaluator defenses.

### Task 9: Run and freeze the 033 paper reference

Run the full adaptive search, compare its best measured trajectory with the
official slope/flips/velocity and best region, characterize variability, and
derive G0–G12 from evidence.

### Task 10: Final validation

Run Python/Bash/JSON/static checks, the full pytest suite, all three Docker
oracles, negative fixtures, alternative-valid solutions, and public-isolation
inspection. Produce one comparison table containing published, regenerated,
absolute/relative error, tolerance, and pass/fail for every headline scientific
quantity. Do not mark incomplete paper runs valid.

