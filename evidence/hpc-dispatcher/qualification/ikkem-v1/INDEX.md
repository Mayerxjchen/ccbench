# site-v1 qualification attempts — evidence index

Migrated 2026-08-25 from the retired `dftworld2-qualification` worktree
(commit `c267f4bda99fae28dda030657ddffeea411cf850`); bytes verified against
`SHA256SUMS`. The original operator scripts are preserved under
`operator-scripts/original-*` for provenance; the maintainable entry points are
`scripts/qualification/run_hpc_dispatcher.sh` and
`scripts/qualification/supervise_hpc_dispatcher.sh`.

## Status

This directory contains **seven attempted** CPU/GPU containment chains against
the <site-alias> HPC site. **There is no sealed `receipt.json`**, so these attempts do
**not** establish `formal_qualified=true`; real-site qualification remains
fail-closed until a verifier-sealed receipt exists. Do not cite this directory
as a passing qualification.

## Attempts

Seven session IDs, each exercised twice (cpu echo-probe containment,
gpu nvidia-probe containment):

| Session | Submitted-run dir (audit + job script) | Fetched logs |
| --- | --- | --- |
| 1564e36e | `run-cpu-echo-probe-containment-1564e36e/`, `run-gpu-nvidia-probe-containment-1564e36e/` | `fetched-cpu-echo-probe-containment-1564e36e/{stdout,stderr}.log` |
| 3d8dc664 | `run-cpu-echo-probe-containment-3d8dc664/`, `run-gpu-nvidia-probe-containment-3d8dc664/` | `fetched-cpu-echo-probe-containment-3d8dc664/{stdout,stderr}.log` |
| 8027a8ef | `run-cpu-echo-probe-containment-8027a8ef/`, `run-gpu-nvidia-probe-containment-8027a8ef/` | `fetched-cpu-echo-probe-containment-8027a8ef/{stdout,stderr}.log` |
| a142edfb | `run-cpu-echo-probe-containment-a142edfb/`, `run-gpu-nvidia-probe-containment-a142edfb/` | `fetched-cpu-echo-probe-containment-a142edfb/{stdout,stderr}.log` |
| ed58a73d | `run-cpu-echo-probe-containment-ed58a73d/`, `run-gpu-nvidia-probe-containment-ed58a73d/` | `fetched-cpu-echo-probe-containment-ed58a73d/{stdout,stderr}.log` |
| f9147f3c | `run-cpu-echo-probe-containment-f9147f3c/`, `run-gpu-nvidia-probe-containment-f9147f3c/` | `fetched-cpu-echo-probe-containment-f9147f3c/{stdout,stderr}.log` |
| fdc0d346 | `run-cpu-echo-probe-containment-fdc0d346/`, `run-gpu-nvidia-probe-containment-fdc0d346/` | `fetched-cpu-echo-probe-containment-fdc0d346/{stdout,stderr}.log` |

Each submitted-run directory carries `audit.jsonl` and the exact
`scripts/job-0001.slurm` that was dispatched; each fetched-log directory holds
the scheduler-side stdout/stderr pulled back from <site-alias>.

## Verification

```bash
cd evidence/hpc-dispatcher/qualification/site-v1
shasum -a 256 -c SHA256SUMS
```

Runtime identity consumed by these runs is frozen in
`reference/runtime/cp2k-runtime.lock.json` and
`reference/runtime/deepmd-jax-runtime.lock.json`.
