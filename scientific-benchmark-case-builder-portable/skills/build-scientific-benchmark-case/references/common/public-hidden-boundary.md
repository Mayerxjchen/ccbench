# Public/Hidden Boundary

The Candidate sees a positive-allowlist bundle; the Verifier and all hidden
assets are physically absent from it.

## Visible by default

```text
public/                  # candidate inputs and instructions
instruction.md           # task prompt
task.toml                # machine-readable submission contract
CONTRACT.md              # evaluation contract
```

Anything not on the allowlist is hidden. `case-design.yaml`, `VALIDATION.json`,
`benchmark_valid.json`, `evaluator-manifest.json`, `evidence/`, `profiles/`,
`reference/`, `solution/`, and `tests/` are hidden.

## Physical absence, not permissions

Hidden assets must be absent from the Candidate bundle. Permissions are not a
boundary: the Candidate process runs inside the sandbox and can read whatever
is on disk. Bundling is copy-then-audit from an allowlist; never
copy-all-then-delete.

## Forbidden content

- hidden reference, solution, tests, thresholds, fixtures;
- expert metrics or answer-specific constants;
- `.git`, old runs, host HOME, credentials, Docker socket;
- symlink/hardlink/path traversal of any visible path;
- semantic leakage: exact hidden hashes, threshold values, expert filenames,
  or workflow answers appearing in visible files.

## Local and HPC share the boundary

Both execution classes use the same hidden-evaluator lifecycle. `local_sandbox`
means isolated local compute inside the Candidate sandbox; an unrestricted
evaluator-host process is development-only and cannot generate official
adversarial evidence. The Candidate is destroyed before a fresh hidden Verifier
runs.

## Prompt fidelity

Every hard verifier outcome is stated abstractly in the instruction. When
alternative valid methods are allowed, the prompt must not disclose the expert
method or its implementation details.
