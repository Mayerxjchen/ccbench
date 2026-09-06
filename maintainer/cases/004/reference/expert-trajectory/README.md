# expert-trajectory/ — HIDDEN expert reference trajectory / oracle strategy

> **DO NOT copy this subtree into `/app`.** The agent must never see it. It is
> the ground truth for building `solution/expert/`, writing the verifier, and
> freezing `reference/thresholds.json`.

This subtree is a provenance-tagged copy of the **real HPC ai2-kit water64
end-to-end potential-development workflow** that ran at `/public/home/<site-user>/ai2kit`
(expert record dated 2026-06-15). Benchmark 034
(`034-ai2kit-water64-end-to-end-potential`) is a controlled, containerized
adaptation of that HPC workflow (see `CONTRACT.md` §1, §10). The agent starts
from the **unrelaxed PACKMOL structure** (`initial/`) and is expected to
reproduce, through its own path, the whole lineage below: relax the structure,
generate first-principles data, train a DeePMD potential, improve it actively,
and validate it.

## The 5-stage lineage (in generation order)

| Stage | Contents |
|-------|----------|
| `initial/` | PACKMOL-built **unrelaxed** 64-H₂O structure (`water64.xyz`), `packmol_water64.inp`, `water.xyz` monomer. **This is the public input structure** (byte copy). |
| `geopt/` | CP2K geometry optimization of the PACKMOL structure (394 BFGS steps → E −1102.58 Ha). Input (`geopt.inp`, `coord_n_cell.inc`, slurm) + full outputs (`water64_geopt-pos-1.xyz`, restart, BFGS.Hessian, `output`). Rationale: a PACKMOL random fill has close contacts; going straight into AIMD is unstable — see `reproduction-notes.md` §1.4. |
| `aimd/` | CP2K **first-principles AIMD** production trajectory. `raw/` = the 4 raw CP2K outputs (pos/frc/cell/ener) + restart; `input/` = `aimd.inp`, `coord_n_cell.inc`, slurm; `processed/` = the 190-frame labeled mother set `aimd.xyz` + `filter.tsv` (step-0 / duplicate / 250–350 K window). **This is the hidden scientific reference: the verifier's hidden E/F and RDF reference derive from it, and the agent never sees it.** |
| `active-learning/` | The ai2-kit CLL loop: `run.sh` + `workflow/` (`setup.sh`, `iter-classic-dp-lammps-cp2k.sh`, `prod-dp-lammps.sh`) + `config/` (`deepmd/`, `lammps/`, `cp2k/`). 4-model committee, `MODEL_DEVI_COND --lo 0.2 --hi 0.4`, 5 iterations. |
| `validation/` | Post-hoc scientific validation: `analysis/` (dp-test on held-out frames, 300 K NVT, RDF comparison, model-deviation plots), `config/nvt/`, `metrics.md` (dp-test E/F + RDF peak table). |

## What is hidden here (must NOT leak into the public prompt)

- The geopt → AIMD → mother-set → 50-frame-sampling → 5-iteration CLL recipe as an
  *schedule* (exact TRAIN_STEPS / MD_STEPS / temperatures / SAMPLE_FREQ / MAX_LABEL).
- 250–350 K temperature window + step-0/duplicate-step removal → 190-frame mother set.
- 4-model committee and `--lo 0.2 --hi 0.4` model-deviation thresholds.
- The final dp-test numbers and RDF peaks used to draft `thresholds.json`.

The **public** `instruction.md` only states the scientific goal and two abstract
stage requirements (generate a first-principles MD reference trajectory with
CP2K; iteratively improve the potential by generating and labeling additional
configurations). All HOW decisions are the agent's.

## Key hidden-architecture facts

- The **190-frame mother set doubles as the hidden scientific validation**:
  because the public input is only the initial structure, the agent generates its
  *own* AIMD and can never train on the expert frames. `hidden-validation/`
  (`dft-validation.extxyz`, `rdf-reference.json`) is generated from
  `aimd/processed/aimd.xyz`.
- A submitted model must pass hidden E/F on `dft-validation.extxyz` (L7), a
  verifier-run 300 K NVT (L8), and the hidden RDF alignment (L9).

## Cross-references

- Machine-readable expert metrics: `../reference.json`
- Draft scientific thresholds (freeze after expert reruns): `../thresholds.json`
- Provenance lock (hashes, software versions, backend declaration): `../source.lock.json`
- Hidden validation generator + outputs: `../hidden-validation/`
