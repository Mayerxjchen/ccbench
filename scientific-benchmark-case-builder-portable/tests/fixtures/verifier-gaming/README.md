# Verifier-gaming fixture

Source case design for adversarial Candidate/Verifier boundary tests. Tests
scaffold a case from this design, then plant a single attack in the Candidate
visible tree and assert `audit_candidate_bundle.py` rejects it.

Attacks covered in `test_common_builder.py::CandidateBoundaryTests`:

- hidden dir smuggled into `public/` (reference, solution, tests, thresholds,
  fixtures);
- symlink / hardlink in the visible tree pointing at hidden material;
- path traversal escaping the case dir;
- semantic leakage of an exact hidden hash into a public file;
- semantic leakage of an expert filename into a public file;
- candidate_visible override importing a hidden root.

The clean scaffold (no planted attack) must audit valid.
