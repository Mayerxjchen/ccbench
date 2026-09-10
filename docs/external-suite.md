# External paper suite contract

`bench` consumes a suite by absolute path; it does not copy the suite
into the benchmark repository. The suite root contains `suite.toml`, and its
`cases_root` points to case directories. Each case keeps the public contract
(`case.toml`, `task.md`, `input/`) separate from operator/runtime material
(`environment/`) and private verifier/reference material (`tests/` or the
legacy `verifier/`, plus `solution/`/`reference/` when present).

```bash
bench suite inspect --root /path/to/paper-suite
bench suite validate --root /path/to/paper-suite
bench runtime list
bench runtime inspect candidate-claude-code
bench mvp export --case /path/to/paper-suite/cases/001-example --out /tmp/public-001
```

The export allowlist is read from the case manifest. By default only the task
instruction and explicitly listed public inputs are placed in the Candidate
workspace; environment, solution, reference, tests, and verifier files are
never exported. A directory name is only a discovery hint: case identity comes
from `case.toml`, and a numeric-prefix mismatch is reported as an error.

The external go-water paper pack has five directories under its own `cases/`.
Readiness belongs to each case: missing scientific references, draft contracts
and unqualified scientific environments must be reported explicitly. Infra
changes do not establish scientific readiness. Unknown but normalized coverage
slugs are accepted as extensions and shown as warnings. Run `suite validate`
before publishing a case contract.

Compute is operator-selected: `local` runs use the chosen local task runtime;
CPU HPC requests route through the IKKEM profile and GPU HPC requests through a
CompShare profile. Candidate containers never receive scheduler credentials.
Formal evaluation additionally requires the existing qualified immutable
images and signed admission; a catalog entry with an empty digest is not a
qualification.
