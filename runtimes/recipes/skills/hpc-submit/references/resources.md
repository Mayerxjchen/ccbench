# Pointers

Both modes decide resources from an abstract `cpu`/`gpu` class — never from a
site's partition or module names.

- **request-only** ceilings and allowed values for this run:
  `compute-capabilities.json`; sizing decision aid: `resource-guidance.md`;
  request file shape: `request-schema.md` (validated by `ccbench mvp
  compute-check`).
- **gateway-execution** allowances and consumption:
  `bench-hpc capabilities` / `bench-hpc usage`; descriptor shape:
  `examples/execution-request.yaml`; lifecycle: `running.md`, `errors.md`,
  `validation.md`.

Site-specific facts (partitions, modules, queues, image digests) are
deliberately invisible: they are the operator's or the gateway's
responsibility. If something you need is not in the manifest or capabilities,
ask the operator.
