# Execution-side preflight and submission record

## Before writing the descriptor

1. Engine scientific preflight passed (structure gates, input sanity,
   convergence parameters). Never submit from structure-generation code.
2. Every input file exists and its sha256/size were computed from the bytes
   on disk. Re-verify after any last edit: a stale digest fails at staging —
   that is the protection working, not a malfunction.
3. Resources sized within advertised capabilities.
4. Runtime image digest pinned (from the engine skill's guidance).

## What to record at submission

Immediately after `bench-hpc submit ... OPERATION_ID ATTEMPT` returns:

- operation id and attempt number
- returned job id (internal provenance)
- descriptor file path
- one line on what this attempt is supposed to test/change

Record these in your working notes (`workflow.md` when present). On any
recovery or rerun, the operation-attempt lineage is what the gateway consults
— never a guess about what "the" job was.

## After completion

`fetch` → engine parser → verdict. Only then record scientific success or
start the next attempt. A `SUCCEEDED` scheduler state with a failed parser
verdict is a failed attempt, and the next attempt must change something.
