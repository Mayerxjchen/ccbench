# Skill, CLI, and Environment Boundaries (P8)

This document formally establishes the boundaries between candidate agents, maintainer tools, skills, and the execution infrastructure.

---

## 1. Skill vs. Infrastructure Boundary

> [!IMPORTANT]
> **Skills are operational instructions and reference manuals for humans and Codex/Agents. The software that is actually invoked by the benchmark infrastructure is the CLI binary, NEVER the Skill itself.**

- **`hpc-submit` Skill**:
  - Available to the Candidate (PAgent).
  - Strictly **provider-neutral**.
  - Exposes only the 7 standard operations: `capabilities`, `submit`, `status`, `logs`, `fetch`, `cancel`, `usage`.
  - Agent declares `compute_class: cpu | gpu` and abstract runtime capabilities (e.g. `cp2k`, `deepmd`, `jax`).
  - Agent is strictly forbidden from declaring, inspecting, or assuming sites, providers, partitions, hostnames, or image IDs.
  - Hidden case assets (e.g. `matclaw-cips`, `thresholds.json`) are strictly prohibited from candidate-visible skills.

- **CompShare Tooling & Maintainer Skills**:
  - Intended solely for **maintainers** and offline manual operations.
  - Documents how maintainers inspect GPU inventory, check account quotas, build custom base images, and diagnose failed nodes.
  - **NEVER installed in candidate environments**.
  - **NEVER invoked by automated benchmark test runners**.

---

## 2. Candidate Environment Isolation

The Candidate evaluation sandbox:
1. **Does not contain `compshare` CLI**: `compshare` binary is omitted from the evaluation container image.
2. **Does not contain cloud credentials**: `COMPSHARE_PUBLIC_KEY`, `COMPSHARE_PRIVATE_KEY`, SSH keys, and cloud tokens live exclusively on the trusted host machine outside the repo.
3. **Restricted network**: Candidate has no direct network access to cloud provider endpoints.
4. **Local control layer**: The candidate executes commands inside its sandbox and talks only to the local `/tmp/bench-hpc.sock` or `bench-hpc` CLI gateway.

---

## 3. Public User vs. Maintainer Profiles

| Target | ComputeProfile | Backing Infrastructure |
|---|---|---|
| **Public Users** | `generic-slurm-v1` | Institutional or local Slurm cluster (CPU + GPU queues) |
| **Maintainers** | `maintainer-hybrid-v1` | Hybrid: CPU -> IKKEM Slurm (嘉庚), GPU -> CompShare Cloud (`compshare ... --json`) |

Public users configure their environment using:
```bash
bench compute configure --out ~/my_compute_profile.json
bench compute validate --profile ~/my_compute_profile.json
```

Maintainers operate within strictly frozen and audited GPU recipes:
- **Image A**: `mlff-matclaw-cips-gpu-v1` (Capabilities: `matclaw-cips`, `runtime.matclaw-gpu` for Cases 031-033)
- **Deferred Image**: `mlff-jax-gpu-v1` (Capability: `runtime.jax` for Case 042)
