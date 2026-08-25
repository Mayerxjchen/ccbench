# MLIP Benchmark Infrastructure Design

**Status:** Approved design baseline, distilled from `/Users/chenxuanjie/Desktop/handoff.md`.

## Goal

Refactor the existing 41-case `dftworld2` benchmark into an auditable,
agent-agnostic benchmark with a common Candidate/Verifier trust boundary and
two execution classes:

- `local_sandbox` for the 37 cases whose scientific computation runs inside
  the Candidate sandbox;
- `hpc_controller` for Cases 031–034, whose Candidate controls scientific
  computation on an evaluator-provided HPC site.

The near-term release is **Ablation-Ready Infrastructure v0**. It must make a
paired No-Skill/With-Skill experiment scientifically interpretable before the
larger Portable Infrastructure v1 migration is attempted.

## Non-goals for Ablation-Ready v0

- Moving all case directories under a new `cases/` root.
- Converting all 41 `task.toml` files to YAML in one change.
- Adding PBS, Kubernetes, or a public leaderboard.
- Proving cross-site score equivalence.
- Changing instructions, scientific references, thresholds, or expert
  solutions.
- Running formal ablation trials before all v0 release gates pass.

## Architecture

```text
                         Case Contract
                               |
                         execution.class
                  +------------+------------+
                  |                         |
           local_sandbox              hpc_controller
                  |                         |
      Candidate = decision+compute   Candidate = decision
                                            |
                                       bench-hpc
                                            |
                                    trusted gateway
                                            |
                                      site adapter
                                            |
                                         real HPC
                  +-------------------------+
                               |
                        raw submission
                               |
                          quarantine
                               |
                     sealed submission + hashes
                               |
                    fresh separate Verifier
                               |
                     Result + immutable record
```

The benchmark is split into:

1. `dftworld_bench` Common Core: contracts, packager, lifecycle, quarantine,
   verifier launch, result taxonomy, and run records;
2. `dftworld_bench.hpc` Optional Extension: job contract, gateway, adapters,
   accounting, and conformance;
3. the existing root-level case directories, migrated incrementally without a
   big-bang rename;
4. `eval.py` as a compatibility CLI that delegates to the package during the
   migration.

## Contract Decisions

### Case manifest

The internal typed model is independent of serialization. During migration the
loader accepts the existing `task.toml` and the future canonical `task.yaml`,
but fails if both are present. It normalizes legacy
`real_hpc_controller` to `hpc_controller` and records that normalization in the
run record. It never infers execution class from a directory name.

Cases 031–034 must explicitly declare `hpc_controller`; all other cases must
explicitly or compatibly resolve to `local_sandbox`.

### Candidate bundle

The trusted packager copies only manifest-allowlisted instruction and public
files. Each rule explicitly maps a case-relative source to a Candidate-relative
destination so legacy `/app/<file>` paths remain stable without parsing a
Dockerfile. It never copies the whole case and then deletes private paths. The
bundle manifest includes normalized relative paths, sizes, SHA-256 values,
case/schema versions, and a deterministic root digest.

### Submission and quarantine

Canonical v2 cases write `final/`. Existing cases whose instructions require
outputs directly under `/app` use an explicit legacy `submission_root = "."`
compatibility mode; the collector removes runtime-only names and records that
legacy layout in provenance. The harness freezes the Candidate, copies the raw
submission to a private host directory, destroys the Candidate, rejects unsafe
filesystem nodes and limits violations, then seals a normalized clean
submission. The Verifier never mounts the Candidate workspace. Moving an old
case to `final/` requires a case-version increment and is not part of the
infrastructure-only refactor.

### Verifier

The Verifier runs as non-root in a fresh container with no network or
credentials. `/submission`, `/tests`, and `/reference` are read-only; `/tmp` is
private; `/logs/verifier` is a fresh writable mount. A verifier result conforms
to the common Result contract and carries case-specific evidence.

### Result taxonomy

Top-level result classes are:

- `VALID_RESULT`: `PASS` or `SCIENTIFIC_FAIL`;
- `AGENT_FAILURE`: timeout, resource exceeded, invalid/no submission, or bad
  input;
- `INFRA_INVALID`: sandbox, harness, gateway/adapter/HPC, or verifier failure.

`INFRA_INVALID` observations are never counted as scientific failures.

### HPC trust boundary

The Candidate does not receive an SSH key, SSH agent socket, site config, or
raw scheduler access. It receives only the `bench-hpc` client and a run-scoped
capability. A trusted gateway owns site credentials and restricts all
operations to the current `run_id`, declared job schema, resource profile, and
remote workspace namespace.

The existing MatClaw controller and Slurm transport are compatibility assets to
wrap behind this contract. They are not the long-term public interface.

## Lifecycle

The authoritative state machine is:

```text
CREATED -> PACKAGED -> CANDIDATE_STARTING -> CANDIDATE_RUNNING
        -> CANDIDATE_STOPPING -> CANDIDATE_FROZEN
        -> SUBMISSION_COLLECTED -> CANDIDATE_DESTROYED
        -> QUARANTINED -> SEALED
        -> VERIFYING -> COMPLETED
```

HPC runs additionally pass through capability issuance, submission disable,
job settlement, and capability revocation. Any transition may enter
`FAILED_AGENT` or `INVALID_INFRA`, but cleanup continues idempotently.

## Migration Strategy

1. Freeze contracts and introduce typed loaders without changing runtime.
2. Implement allowlist packaging and separate verification.
3. Add lifecycle, quarantine, result, and immutable run record.
4. Migrate `001-hello` and `009-cp2k-run` as Local reference cases.
5. Wrap the current HPC machinery behind the stable job/gateway contract.
6. Migrate 034, then 032, 031, and 033.
7. Run paired one-replicate pilot trials; do not include them in formal data.
8. Freeze the v0 release manifest and conduct formal multi-replicate ablation.
9. After the experiment, complete all Local migrations and a second-site
   portability demonstration for Portable Infrastructure v1.

## Release Gates

Ablation-Ready v0 requires:

- explicit execution class for 031–034;
- deterministic public-only Candidate bundles;
- fresh Candidate HOME/workspace/session;
- Candidate destruction before a fresh Verifier starts;
- quarantine and sealed submission digest;
- immutable case, skill, image, profile, submission, verifier, and platform
  identities in the run record;
- No-Skill/With-Skill differing only in physical Skill availability;
- run-scoped HPC capability with no raw scheduler credential in Candidate;
- correct separation of valid scientific failure, Agent failure, and
  infrastructure invalidity;
- passing Local and HPC pilot trials with pilot data excluded from the formal
  experiment.

Portable Infrastructure v1 additionally requires all 37 Local cases on the
common contract, all four HPC cases on the stable extension, adapter
conformance, and reproduction on a second independent HPC site without case or
Verifier changes.
