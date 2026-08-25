# Hidden evaluator (Case 033)

`verifier.py` is the single implementation of the analysis and acceptance checks;
`reference/generate_reference.py` is a thin CLI wrapper over it, so there is only
one copy of the logic to maintain.

## Security model

- Inspection is limited to the graded submission directory (default `/app`);
  `reference/`, `public/`, and host paths are never read at grading time.
- `test.sh` runs `test_outputs.py` under the pinned image python and writes the
  harness reward to `/logs/verifier/reward.txt`.
- Every metric is recomputed from delivered raw trajectories (flips, slope,
  sequential propagation, best row). The paper protocol's chronology is enforced
  with an independent digest: every round after the first must record
  `decision_input_sha256` equal to the SHA-256 of the canonical JSON summary of
  all preceding measured jobs, so the fourteen-job path cannot be replayed ahead
  of the measurements it depends on. Paper rounds must be contiguous 1..7 with
  exactly two jobs each and a demonstrated sequential slope.
- `EXPECTED_PROFILE` is taken from `MATCLAW_PROFILE` (pinned to `paper` by the
  task contract) so a submission graded under `paper` cannot pass as a smoke run.
- Negative fixtures in `test_outputs.py` tamper a disposable copy of the real
  submission and must be rejected with an explicit error, never an exception.
