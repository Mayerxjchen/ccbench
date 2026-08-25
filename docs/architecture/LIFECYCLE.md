# Benchmark Lifecycle (v0)

**Status:** Normative. The authoritative state machine for every attempt.

## Canonical sequence

```text
CREATED -> PACKAGED -> CANDIDATE_STARTING -> CANDIDATE_RUNNING
        -> CANDIDATE_STOPPING -> CANDIDATE_FROZEN
        -> SUBMISSION_COLLECTED -> CANDIDATE_DESTROYED
        -> QUARANTINED -> SEALED
        -> VERIFYING -> COMPLETED
```

**The Verifier never starts while the Candidate is alive.** `VERIFYING` is
reachable only after `CANDIDATE_DESTROYED`.

## States

- `CREATED` — attempt allocated with a fresh `run_id`.
- `PACKAGED` — allowlisted public bundle produced; bundle digest known.
- `CANDIDATE_STARTING` / `CANDIDATE_RUNNING` — Agent turn.
- `CANDIDATE_STOPPING` — new writes disabled; jobs/capabilities disabled (HPC).
- `CANDIDATE_FROZEN` — Candidate suspended; submission declared.
- `SUBMISSION_COLLECTED` — raw submission copied to a private host dir.
- `CANDIDATE_DESTROYED` — Candidate container removed.
- `QUARANTINED` — unsafe nodes rejected; limits enforced.
- `SEALED` — normalized clean submission sealed with manifest digest.
- `VERIFYING` — independent Verifier runs against the sealed submission.
- `COMPLETED` — result classified and persisted to the run record.
- `FAILED_AGENT` / `INVALID_INFRA` — terminal failure classes (see
  `RESULT-TAXONOMY.md`), enterable from any transition.

## Rules

1. **No backward transitions.** A phase cannot be re-entered after it is left.
2. **No direct jumps.** Every transition is an explicit legal edge.
3. **Cleanup is idempotent.** Teardown is attempted after timeout, Agent
   exception, and harness failure; partial cleanup never blocks a later retry.
4. **Deadlines.** Agent wall-clock, walltime, and submission-size limits are
   enforced by the Harness/quarantine, not by the Agent.
5. **Immutable events.** Every transition is an event `{phase, utc_timestamp,
   reason?}` appended to the run record; the history is never rewritten.

## HPC runs additionally

```text
CAPABILITY_ISSUED -> (during CANDIDATE_RUNNING)
SUBMISSIONS_DISABLED -> (during CANDIDATE_STOPPING)
JOBS_SETTLED -> (queued/running jobs resolved or cancelled)
CAPABILITY_REVOKED -> (after JOBS_SETTLED, before CANDIDATE_DESTROYED)
```

Capability revocation precedes destruction so the Candidate can never resume
an HPC action during quarantine.
