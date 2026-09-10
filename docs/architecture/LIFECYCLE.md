# Run lifecycle

The runner creates a fresh run directory, exports allowlisted public input, records effective configuration and budgets, and starts Candidate plus its model Gateway.

Candidate completes one phase and is cleaned up before evaluation. The ordering invariant is `CANDIDATE_DESTROYED` before `VERIFYING`: an independent verifier must never share the live Candidate environment.

A compute request pauses the run at `COMPUTE_REQUIRED`. Operator output import and resume continue the same session, while preserving previous phase usage and immutable input identities. Missing or inconsistent phase accounting cannot become a fresh zero budget.

A completed submission is quarantined and sealed. The verifier reads the sealed bytes in a fresh, networkless container. Its result is normalized into a scientific outcome, agent failure or infrastructure-invalid observation.

Timeouts and interrupts retain available telemetry and trigger teardown. Failed or incomplete phases cannot silently resume as if they had completed. Status, lifecycle events and messages remain visible in the run directory; the operator must retain these artifacts with the scored result.
