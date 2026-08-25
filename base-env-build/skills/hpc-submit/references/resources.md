# Pointers

- Everything about your run's allowances: `bench-hpc capabilities`.
- Everything about consumption so far: `bench-hpc usage`.
- Descriptor shape: `examples/execution-request.yaml`.
- Engine-specific launch lines, checkpoints, and parser behavior live in the
  engine skills (cp2k, lammps, deepmd) — not here.

Site-specific facts (partitions, modules, queues) are deliberately invisible:
they are the gateway's responsibility. If something you need is not in
capabilities, ask the operator.
