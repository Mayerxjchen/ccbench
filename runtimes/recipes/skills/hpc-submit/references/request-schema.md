# Compute request schema (request-only mode)

> This is the **Mode A (request-only)** artifact of the `hpc-submit` skill.
> Write one per external step under `compute-requests/`. This mode never
> submits; the operator maps the abstract request onto a real platform. The
> gateway-execution (Mode B) job descriptor is a different artifact — see
> `running.md` and `examples/execution-request.yaml`.

A compute request is a single JSON object that turns one scientific step into
an **abstract, verifiable** description. It is data, not a shell command or a
credential channel. The operator validates it with
`ccbench mvp compute-check` (authoritative) and then maps it onto a real
platform. The repository also keeps a shape-only JSON Schema at
`schemas/compute-request.schema.json` for editor tooling.

## Where the file lives

Write the request under the run's mutable compute directory:

```text
compute-requests/request-001.json
```

A request anywhere else is rejected.

## Top-level fields

| Field | Kind | Required | Meaning |
|---|---|---|---|
| `schema_version` | `"1.0"` | yes | Contract version. Anything else is rejected. |
| `compute_class` | `"cpu"` \| `"gpu"` | yes | The only compute decision. The operator maps this to a backend. |
| `command` | array of strings | yes | Non-empty argv. No shell metacharacters, no scheduler flags. |
| `resources` | object | yes | Per-node resource request (see below). |
| `inputs` | array | yes | Exactly the files the operator must stage; each with its exact SHA-256. |
| `outputs` | array of paths | yes | Files the operator must return, all under `compute-results/`. |
| `validation` | object | no | Optional evidence phrases for a scientific verdict. |

### `command`

The first element is the executable, the rest are arguments. This is **argv**,
not a shell line: no `&&`, `|`, `>`, `;`, `$VAR`, backticks, or globs the
operator would have to interpret. If a step needs an interpreter, say so
explicitly (`python`, `python scripts/run.py`, …).

### `resources` (per node)

| Key | Kind | Range | Meaning |
|---|---|---|---|
| `nodes` | integer | >= 1 | Number of compute nodes. |
| `ntasks` | integer | >= `nodes` | Number of tasks total. |
| `cpus_per_task` | integer | >= 1 | CPUs per task. |
| `memory_gb_per_node` | integer | >= 1 | RAM budget **per compute node**, not a flat total. |
| `gpus` | integer | >= 0 | GPUs per node. |
| `minimum_gpu_memory_gb` | integer | >= 1, **GPU only** | Minimum memory per GPU the model needs. Must be absent for `cpu`. |
| `walltime_min` | integer | >= 1 | Upper bound on wall-clock minutes. |

Mechanical rules enforced by the validator (fail-closed; a violation rejects
the request, it is not repaired):

- Unknown `resources` keys are rejected.
- `cpu` requires `gpus == 0` and must **not** set `minimum_gpu_memory_gb`.
- `gpu` requires `gpus >= 1` and **must** set `minimum_gpu_memory_gb >= 1`.
- `ntasks >= nodes`.

Read `compute-capabilities.json` for the resource ceilings and the allowed
per-node memory / GPU memory values on offer. Stay inside them: request the
smallest shape that can actually finish the step.

### `inputs`

```
"inputs": [
  { "path": "work/run_cpu.py", "sha256": "9f2c…" }
]
```

Every input must already exist in the workspace, must not be a symlink or an
escape, and its `sha256` must equal the file's real digest. Never invent a
digest — compute it with a checksum command on the actual file. The operator
stages exactly these files.

### `outputs`

```
"outputs": ["compute-results/cpu/result.json"]
```

Each output is a relative file path that **must** resolve under
`compute-results/` (at least two path segments, e.g.
`compute-results/<step>/<file>`). The operator copies back only these declared
outputs.

### `validation`

```
"validation": {
  "success_markers": ["converged", "force RMS < 1e-3"],
  "reject_if": ["NaN", "MAX_STEPS"]
}
```

Optional but strongly encouraged. These are phrases in the real engine output
that make a run's verdict scientific rather than scheduler-state-only. The
skill never treats a completed scheduler state as success; it inspects the
actual output the operator returns.

## Examples

### CPU request

```json
{
  "schema_version": "1.0",
  "compute_class": "cpu",
  "command": ["python", "work/run_cpu.py", "input/system.json"],
  "resources": {
    "nodes": 1,
    "ntasks": 8,
    "cpus_per_task": 2,
    "memory_gb_per_node": 32,
    "gpus": 0,
    "walltime_min": 60
  },
  "inputs": [
    { "path": "work/run_cpu.py", "sha256": "<64 lowercase hex>" },
    { "path": "input/system.json", "sha256": "<64 lowercase hex>" }
  ],
  "outputs": ["compute-results/cpu/result.json"],
  "validation": {
    "success_markers": ["converged"],
    "reject_if": ["NaN"]
  }
}
```

### GPU request

```json
{
  "schema_version": "1.0",
  "compute_class": "gpu",
  "command": ["dp", "train", "work/train_input.json"],
  "resources": {
    "nodes": 1,
    "ntasks": 1,
    "cpus_per_task": 8,
    "memory_gb_per_node": 64,
    "gpus": 1,
    "minimum_gpu_memory_gb": 24,
    "walltime_min": 240
  },
  "inputs": [
    { "path": "work/train_input.json", "sha256": "<64 lowercase hex>" }
  ],
  "outputs": ["compute-results/train/model.ckpt"],
  "validation": {
    "success_markers": ["Training complete"],
    "reject_if": ["CUDA_OUT_OF_MEMORY", "NaN loss"]
  }
}
```

Both examples validate against `schemas/compute-request.schema.json` and
against `ccbench mvp compute-check`.
