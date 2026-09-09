---
name: bench-compute-request
description: "Decide whether a step needs external CPU/GPU compute and write a validated, request-only compute descriptor."
---

# Bench compute request (Candidate)

This is a request-only skill. The Candidate may decide that a task needs
external compute and write a descriptor under `compute-requests/`; an external
Operator performs the execution and returns declared files under
`compute-results/`.

## Procedure

1. Run inexpensive parsing and preflight locally. Do not create a request for
   work that fits the workspace.
2. Select the smallest abstract class, `cpu` or `gpu`, based on scientific
   need. GPU use requires a scientific justification.
3. Write a request draft containing input paths only. The trusted host adds
   SHA-256 digests and sizes after you exit; never self-report those fields.
   Commands are argv arrays, never shell strings:

```json
{
  "schema_version": "1.0",
  "compute_class": "cpu",
  "command": ["program", "--input", "input.dat"],
  "resources": {
    "nodes": 1, "ntasks": 1, "cpus_per_task": 4,
    "memory_gb_per_node": 8, "gpus": 0, "walltime_min": 30
  },
  "inputs": [{"path": "input.dat"}],
  "outputs": ["compute-results/output.dat"],
  "validation": {"success_markers": ["SUCCESS"]}
}
```

Do not run a validator or external command. The trusted host validates and
seals the canonical request. CPU requests must set `gpus=0`
and omit GPU memory; GPU requests must set `gpus>=1` and a positive
`minimum_gpu_memory_gb`. Memory is per node. Outputs must stay under
`compute-results/`.

## Hard boundaries

Never submit, monitor, fetch, cancel, or run external work. Never invoke a
remote-login client, scheduler CLI, gateway client, or cloud-management
command. Never mention or emit hosts, partitions, accounts, credentials,
tokens, image IDs, or concrete site paths. Do not fabricate outputs or
digests. At most two evidence-based resource revisions are allowed for a
step; scheduler completion is not scientific success.
