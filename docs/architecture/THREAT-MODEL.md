# Benchmark Threat Model (v0)

**Status:** Normative. Approved by design baseline `2026-08-17-mlip-benchmark-infrastructure-design.md`.

## Roles

| Role | Trust | Definition |
|---|---|---|
| **Candidate** | **Untrusted** | The agent's execution container. It sees only the allowlisted public bundle, the frozen sandbox, and (for HPC cases) the `bench-hpc` client. |
| **Harness** | Trusted | Control plane that drives lifecycle, packaging, quarantine, Verifier launch, and run-record persistence. |
| **Gateway** | Trusted (HPC boundary) | Owns site credentials and scheduler transport. Enforces run-scoped capability. |
| **Adapter** | Trusted | Translates the stable job contract into a site scheduler (Slurm/PBS/local). |
| **Compute Runtime** | Trusted (frozen) | Frozen OCI image carrying the scientific environment. Digest-pinned. |
| **Quarantine** | Trusted | Rejects unsafe submission filesystem nodes and limits violations; seals a clean submission. |
| **Verifier** | Trusted, independent | Fresh, isolated, networkless container that reads only the sealed submission. |
| **Evaluator** | Trusted | Provides the HPC site, site credentials, and resource budget. |

## Trust assumptions

- **The Candidate and its submission are untrusted.**
- The Harness is the only trusted controller of sandbox lifecycle.
- The Gateway, not the Candidate, holds HPC credentials.
- The Verifier never runs inside, inherits from, or shares a workspace with the Candidate.
- Scientific state (reference, hidden tests, thresholds, solutions) never enters a Candidate image or bundle.
- A frozen compute runtime is the only scientific software surface.

## Common threats (T1–T7)

| # | Threat | Failure | Mitigation |
|---|---|---|---|
| T1 | Answer leakage | Hidden reference/solution/thresholds reach Candidate | Positive allowlist packaging; forbidden-name leak scan; no whole-case copy. |
| T2 | Submission tampering | Agent edits declared outputs after freeze | Freeze Candidate, collect into private raw dir, destroy Candidate, then seal. |
| T3 | Environment drift | Stale state persists across trials | Fresh container, fresh HOME, fresh workspace, fresh Agent session per trial. |
| T4 | Unsafe submission | Symlink/FIFO/socket/device/archive smuggling | Quarantine lstat validation; normalization; atomic seal. |
| T5 | Verifier contamination | Verifier inherits Candidate-modified environment | Separate Verifier container; read-only mounts; no Candidate workspace mount. |
| T6 | Forged provenance | Run record claims work that did not happen | Append-only immutable run record written by the Harness, not the Agent. |
| T7 | Resource abuse | Agent exceeds declared limits | Resource profile enforced by the Harness/gateway; accounting in run record. |

## HPC threats (H1–H6)

| # | Threat | Failure | Mitigation |
|---|---|---|---|
| H1 | Credential theft | Candidate obtains SSH key/socket/site config | Candidate receives only `bench-hpc` + run-scoped bearer token; no SSH material mounted. |
| H2 | Cluster-trivia dependence | Scores depend on partition names/paths | Agent writes a portable `job.yaml`; adapter translates to site-specific directives. |
| H3 | Cross-run isolation | One run reads another run's jobs/files | Gateway namespaces everything under `<run_id>`; adapter-private 0700 job dirs. |
| H4 | Job forgery | Fake job IDs / fabricated results | Gateway-owned adapter state; idempotency keys; settlement only from adapter state. |
| H5 | Accounting evasion | Resource usage undercounted | Gateway ledger records requested vs used; included in run record. |
| H6 | Gateway compromise | Gateway leaks credentials | Gateway container isolated on per-run internal network; egress separated; credentials never in logs/records. |

## Non-goals (v0)

- The Candidate is not assumed to be resistant to an adversarial human Evaluator reading the Verifier (see separate public/evaluator kit split for leaderboards).
- Site adapter conformance, not site trust, is the portability guarantee.
