# Hidden evaluator (Case 031)

`verifier.py` is the single implementation of the analysis and acceptance checks;
`reference/generate_reference.py` is a thin CLI wrapper over it, so there is only
one copy of the logic to maintain.

## Security model

- Inspection is limited to the graded submission directory (default `/app`);
  `reference/`, `public/`, and host paths are never read at grading time.
- `test.sh` runs `test_outputs.py` under the pinned image python and writes the
  harness reward to `/logs/verifier/reward.txt`.
- Every scientific claim is recomputed from delivered raw artifacts: teacher
  labels are re-derived with the locked teacher model, held-out MAE is recomputed
  from the delivered `student.pb` models, data growth is checked exactly, and
  teacher-selected configurations must be a subset of the frames the student
  actually explored (recomputed from exploration trajectories).
- The active step is audited: `exploration_attempts`, `selected_frames`,
  `training_frames_before/after`, and `selected_configuration_hashes` must be
  self-consistent and match the recomputed dataset growth exactly. A paper run
  must complete at least one full selection/relabel/retrain cycle, so an
  iteration-0 convergence with no active step is rejected.
- `EXPECTED_PROFILE` is taken from `MATCLAW_PROFILE` (pinned to `paper` by the
  task contract) so a submission graded under `paper` cannot pass as a smoke run.
- The per-iteration exploration plan (temperatures, batches, attempt cap) lives
  in the hidden `solution/run_profiles.json`, never in `public/`; the run is
  resumable only when identity and every committed artifact hash still match
  (`checkpoint.json`). `solution/alt_distillation.py` is an independently-written
  solver (different deviation metric and exploration scheme) graded by the same
  verifier — the G11 alternative-validity evidence.
- Negative fixtures in `test_outputs.py` tamper a disposable copy of the real
  submission and must be rejected with an explicit error, never an exception.
