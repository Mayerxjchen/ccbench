# Evidence Retention Policy

Git stores manifests; large bytes are stored as content-addressed primary and
replica objects. `evidence/manifest.json` binds every release gate to
restorable bytes.

## Object model

- Objects are content-addressed by `sha256:<hex>`.
- A manifest entry records `rel_path`, `size`, and `sha256` for a restorable
  byte object; replicas are content-addressed the same way.
- Every gate G0..G12 is bound to at least one object in `manifest.gates`.

## Finalization

Release finalization requires, in order:

1. upload of the byte objects,
2. read-back hash verification,
3. a fresh restore from the stored objects,
4. reverification that the restored bytes match the recorded hashes.

Only `check_release.py` derives `benchmark_valid=true`, from sealed, restorable
evidence.

## Failure classification

- Missing byte, hash mismatch, open gate, draft threshold, evaluator drift,
  Candidate leak, insufficient reference reproducibility, or a non-restorable
  bundle all force release invalidity (fail-closed).
- Infrastructure invalidity (storage loss, scheduler outage) is not a
  scientific failure: it is recorded separately and never reads as an Agent
  correctness verdict.
