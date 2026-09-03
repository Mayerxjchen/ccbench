# Architecture Freeze — 2026-09-03

**Status:** Normative change-control record. Complements
`HPC-CONTRACT-v1.md`, `CASE-STANDARD.md`, and the capability-matrix spec
(`docs/superpowers/specs/2026-09-02-capability-matrix-qualification-design.md`).
This ADR freezes what the current improvement cycle **will and will not**
change. Work that contradicts this file is out of scope until the freeze is
superseded by a new reviewed ADR.

## 1. Scope and Exclusions this cycle

- **Phase 7 is Case Migration (031–034, 042)** — active and required for execution.
- **NS/WS pilot comparisons** — DEFERRED. Not executed, not sampled, no pilot
  statistics produced. Release gates per case still run in full.
- **CompShare Driver** — Maintainer profile ONLY (`maintainer-hybrid-v1`); not a
  public user GPU backend. External users default to generic Slurm HPC.

## 2. External execution model: Local / HPC only

- To users, Cases have exactly two kinds: **Local** and **HPC**.
- `local_sandbox` / `hpc_controller` remain **internal compatibility enums**
  during migration only; they must not appear in new user-facing docs, case
  templates, or CLI surfaces. Case-specific HPC infra code trends to zero.
- 031–033 keep `benchmark_valid=true`; their scientific content and verifiers
  are untouched — only the control path converges onto the unified
  `bench-hpc` gateway.

## 3. Runtime boundary (target contract)

- The Agent's `ExecutionRequest.runtime` names a **capability**
  (`cp2k`, `deepmd`, `lammps`, `ai2kit`, `matclaw-cips`), never a SIF path or
  digest.
- The trusted registry + SiteProfile resolve a `ResolvedRuntime`
  `{capability, sif_path, sif_sha256, runtime_profile_digest, qualification}`;
  digests appear only in ResolvedRequest, Slurm evidence, and RunRecord.
- Unknown/unresolvable capability fails closed. The digest-shaped runtime is
  kept only as a hidden compatibility path (removal deferred to Phase 8).

## 4. AI2Kit is a runtime, not a control plane

- Canonical identity: `ai2kit-runtime-v1`; capability `runtime.ai2kit`.
- No "AI2Kit controller" framing anywhere in normative docs/locks: it never
  holds SSH credentials and never issues raw `sbatch`. Scheduling and resource
  policy stay with the trusted infra (HpcDispatcher/gateway); the runtime only
  executes AI2Kit workflow code.
- One shared runtime, not per-case images.
- Lineage (decision 2026-09-03, R2): the original controller image and its
  docker archive are unrecoverable (archive deleted post-acceptance; build
  recipe never committed). The runtime is a **derived** artifact: rootfs
  content derived from the trusted CP2K SIF
  (`05f708b1…5cfef5dd`, itself a conversion of image `8a840aa2e477…`), and
  locks must record parent SIF SHA, Apptainer version, rootfs manifest digest,
  imported image digest, Dockerfile digest, archive digest, final SIF SHA, and
  observed software versions — and must **not** claim restoration of the
  original image.
- Known probe defect (must fix in Phase 2 before any merge): `ai2_kit` 1.1.0
  ships an empty `__init__.py` (no `__version__`); the frozen canary's
  `getattr(mod, "__version__", "?")` assertion can never pass. Version probe
  must use `importlib.metadata.version("ai2_kit")` with the attribute read as
  fallback.

## 5. Site / compute-class rules

- Private `cluster_profile.toml` lives outside the repo (template in
  `scripts/hpc/`); SiteProfile is credential-free.
- Full-GPU and **MIG are separate profiles and separate qualifications**;
  `gpu,gpu-mig-*` mixed partitions are not a formal compute class. MIG results
  never substitute for full-GPU capability evidence.

## 6. Frozen — do not rewrite this cycle

Gateway/Adapter internals; multi-scheduler support (PBS/LSF/Kubernetes);
automatic scientific retry; automatic fetch; forced four-enum ResultClass
rewrite (three internal classes + `SCIENTIFIC_FAIL` code is semantically
equivalent); new controllers/DPDispatcher; per-case runtimes.

## 7. Freeze / re-seal discipline

Phases 1–2 change the qualification trust boundary, so
`qualification-candidate-site-v2` is retired as the final freeze; a new
candidate tag (`qualification-candidate-site-v3`) is cut after Phases 1–3, and
real-site qualification re-seals under it. Phase 8 legacy deletion happens
only after the new path is proven end-to-end; migration readers stay.
