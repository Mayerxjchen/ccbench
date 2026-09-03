# ADR 2026-09-03: Final Benchmark Infrastructure & Case Migration Architecture

**Status:** Accepted & Normative.
**Date:** 2026-09-03
**Supersedes/Refines:** `docs/architecture/ARCHITECTURE-FREEZE-2026-09-03.md`

---

## 1. Context and Problem Statement

The benchmark infrastructure must provide a secure, reproducible, and cloud/HPC-portable evaluation environment for autonomous scientific AI agents.
To prevent credential leakage, platform lock-in, and configuration tampering:
- The Candidate/Agent must never see provider, cluster, partition, region, or image IDs.
- The Candidate specifies only abstract `compute_class` (`cpu` or `gpu`) and high-level `runtime` capabilities (`cp2k`, `deepmd`, `jax`, `lammps`, `ai2kit`).
- The trusted infrastructure resolves these abstract requests into concrete backend actions via two distinct layers: **ComputeProfile (Routing)** and **SiteProfile (Site Policy & Execution)**.

---

## 2. Key Architecture Decisions

### 2.1 External Execution Model: Local vs HPC Only
- To users and Candidates, Cases are strictly classified as **Local** or **HPC**.
- Internal legacy designations (`local_sandbox`, `hpc_controller`) are deprecated shims and will be completely removed in P10.
- All HPC cases converge onto the unified `bench-hpc` gateway facade.

### 2.2 Maintainer Profile vs Public External Profile
- **Maintainer Profile (`maintainer-hybrid-v1`)**:
  - `routes.cpu` -> IKKEM Slurm HPC.
  - `routes.gpu` -> CompShare Cloud GPU.
  - **Crucial Rule**: CompShare is an internal Maintainer implementation detail for official benchmark runs. **CompShare is NOT a public user GPU backend.** It is never exposed in the Quick Start or public user documentation.
- **External User Profile (`user-slurm-v1`)**:
  - External users default to their own institutional or private Slurm HPC cluster.
  - Public documentation guides users exclusively on configuring their standard Slurm cluster for CPU and/or GPU queues via `mlffbench compute configure`.
  - The `SlurmDriver` is generic, portable, and free of IKKEM-specific or MatClaw-specific assumptions.

### 2.3 Phase Scope: P7 is Case Migration; NS/WS Pilot is DEFERRED
- **P7 is Case Migration (031–034, 042) and MUST BE EXECUTED**:
  - Cases 031, 032, and 033 must migrate from legacy controller scripts to the unified `bench-hpc` protocol.
  - Case 034 (AI2Kit + CP2K + DeepMD/LAMMPS) migrates to CPU route (CP2K) + GPU route (DeepMD/LAMMPS) with explicit Agent data staging; achieves `benchmark_valid=true`.
  - Case 042 (GO-Water DPMP JAX) migrates to CPU route (CP2K) + GPU route (JAX/DPMP); achieves `benchmark_valid=true`.
- **NS/WS Pilot is explicitly DEFERRED**:
  - No NS/WS pilot comparisons will be executed, sampled, or checked this cycle.

### 2.4 Separation of Concerns: ComputeProfile and SiteProfile
- **ComputeProfile**:
  - Routing identity mapping `compute_class` (`cpu` | `gpu`) -> `site_profile` name.
  - Generates immutable digest bound to `RunLock`.
  - Strips all site/provider details from public view.
- **SiteProfile**:
  - Concrete site execution policy (scheduler, account, partition/queue, memory/cpu ceilings, Apptainer/Singularity paths, image locks).
  - Credential-free; credentials injected by trusted harness only.
- **Runtime Resolution**:
  - Agent supplies capability token (`deepmd`, `cp2k`, etc.).
  - Gateway resolves to `ResolvedRuntime` (SIF path + sha256 for Slurm; frozen ImageId for CompShare).
  - Agent cannot supply digests or image IDs.

### 2.5 Strict Driver & Security Invariants
- **CompShareDriver (Maintainer Only)**:
  - Run-scoped single GPU instance: max 1 instance per Run, max 1 GPU per instance, max 1 active GPU operation per Run.
  - Different Runs never share instances.
  - Operations within the same Run reuse the active instance.
  - Mandatory stop and termination during settlement.
  - Recycling failure logs to orphan ledger and produces `INFRA_INVALID`.
  - Candidate never receives API keys or instance credentials.
- **SlurmDriver (Public & Portable)**:
  - Standardized sbatch generation, no hardcoded site checks (`no if site_id == ikkem`).
  - Distinguishes full GPU from MIG into separate resource classes.
  - Explicit staging and fetch with SHA256 integrity verification.

### 2.6 Two-layer Qualification
1. **Site Qualification**: Independent qualification proof for each site (IKKEM, CompShare, Generic Slurm).
2. **ComputeProfile Qualification**: Composite qualification binding verified CPU and GPU site qualification digests.
   Official evaluation results require `maintainer-hybrid-v1` qualification; external results bind user's qualified ComputeProfile.

---

## 3. Implementation Roadmap (P0 to P10)

- **P0**: Baseline freeze, ADR formalization, clean tree hygiene.
- **P1**: `ComputeProfile` schema, dataclass, loader, `ComputeRouter`, RunLock integration.
- **P2**: Route-aware `ResolvedRuntime` supporting both SIF and CompShare image targets.
- **P3**: Maintainer `CompShareDriver` implementation with run-scoped lifecycle and budget guards.
- **P4**: Maintainer GPU image qualification (`mlff-deepmd-gpu-v1`, `mlff-jax-gpu-v1`).
- **P5**: Public Generic `SlurmDriver` decoupling and example profiles.
- **P6**: Two-layer qualification pipeline and freeze point `qualification-candidate-compute-profile-v1`.
- **P7**: Case migration for 031–034 and 042 onto unified `bench-hpc` protocol.
- **P8**: Public CLI (`mlffbench compute`), user documentation, and provider-neutral `hpc-submit` skill.
- **P9**: Release Gates (G0–G15) end-to-end verification.
- **P10**: Legacy cleanup (removal of `matclaw_hpc_controller.py`, legacy gateway shims, and compatibility bridges).
