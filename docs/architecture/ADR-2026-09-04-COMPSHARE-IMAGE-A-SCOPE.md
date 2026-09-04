# ADR-2026-09-04: Freeze the first CompShare image qualification scope

## Status

Accepted for the offline Gate B/C preparation work. No cloud mutation is
authorized by this decision.

## Decision

The first CompShare custom image, **Image A**, is the `runtime.matclaw-gpu`
profile and is qualified only for cases 031, 032, and 033. The first scope is
therefore:

| Image | Runtime capability | Cases | Backend |
| --- | --- | --- | --- |
| Image A | `runtime.matclaw-gpu` | 031, 032, 033 | CompShare GPU |

Case 034 is excluded from Image A. Its current DeepMD/AI2Kit execution remains
on its existing CPU/Slurm route until a separate operation-level GPU route is
implemented and qualified. Case 042 is excluded from Image A and requires a
separate JAX image/profile in a later phase.

The qualification scope is an allowlist, not a claim that an image has already
been built or qualified. The repository's Image A runtime lock remains
unbuilt/unqualified until a real Gate B construction and Gate C fresh-instance
receipt are independently completed.

## Failover and RunLock invariants

`failover_standby` is an operator note, never an execution input. Failover is
manual only:

1. the operator ends and settles the old run;
2. the operator selects an already-qualified profile for the standby site;
3. a new run is opened with a new, explicitly resolved lock.

The running RunLock never drifts to a standby region, zone, GPU type, image,
or profile. There is no automatic retry, cross-region migration, or implicit
profile substitution. A run that cannot be completed with its locked profile
fails closed and must be restarted explicitly after operator review.

## Consequences

- Image A's recipe and qualification evidence must cover 031–033 only.
- 034 and 042 cannot be used as evidence that `runtime.matclaw-gpu` is
  qualified.
- Any future standby configuration must be represented outside the execution
  request and outside the existing RunLock; selecting it requires a new
  operator-authored resolution.
- Gate B remains construction work and Gate C remains fresh-runtime
  qualification work. Neither is implied by this ADR.
