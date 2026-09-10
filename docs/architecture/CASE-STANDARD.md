# Case contract

```python
execution_classes = {"local_sandbox", "hpc_controller"}
```

New paper cases live outside the infra repository. Each case provides `case.toml`, `task.md`, `input/`, `environment/` when needed, and a private `verifier/` bundle. Private `solution/` and `reference/` may live under that verifier bundle. A paper `suite.toml` locates its cases.

Case identity and execution class come from the manifest. Directory names only aid discovery. Existing task manifest compatibility is implemented by `bench.contracts.case`; new suites use the explicit case contract.

Candidate input is built by a positive allowlist. Instruction becomes `instruction.md`; each public mapping names a case-relative source and workspace-relative destination. Unsafe links, special files, path escapes and destination collisions are rejected. No whole-case copy is allowed.

Candidate writes `work/`, `final/`, `compute-requests/` and `compute-results/`. Only the declared submission is sealed and sent to the independent verifier. Changing scientific instructions, thresholds, references or solutions requires reviewing the case version and comparison conditions.

Runtime images are reusable capabilities. A new paper referencing the same qualified image does not need a new image build. Site credentials and run outputs belong to the operator, outside the public case bundle.
