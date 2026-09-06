# Hidden evaluator (Case 032)

`verifier.py` is the single implementation of the analysis and acceptance checks;
`reference/generate_reference.py` is a thin CLI wrapper over it, so there is only
one copy of the logic to maintain.

## Security model

- Inspection is limited to the graded submission directory (default `/app`);
  `reference/`, `public/`, and host paths are never read at grading time.
- `test.sh` runs `test_outputs.py` under the pinned image python and writes the
  harness reward to `/logs/verifier/reward.txt`.
- Every claim is recomputed from delivered raw trajectories: `Q(T)=mean(|eta|)`
  and both Curie estimators are recomputed per trajectory, per-trajectory SHA-256
  is enforced, partial trajectory files are rejected outright, and (for the paper
  profile) the pilot plus the exact 13-temperature grid with exact atom/frame
  counts and finite frames are required. The paper result must also land within
  the source tolerance of the reference Tc.
- `EXPECTED_PROFILE` is taken from `MATCLAW_PROFILE` (pinned to `paper` by the
  task contract) so a submission graded under `paper` cannot pass as a smoke run.
- Negative fixtures in `test_outputs.py` tamper a disposable copy of the real
  submission and must be rejected with an explicit error, never an exception.

## Resumable solution (`solution/`)

The paper protocol — which temperatures to measure, the coarse/near-transition
split, and the 350 K pilot — lives in the hidden `solution/run_profiles.json`
(keys `smoke`/`paper` + `*_alt` sections), never in `public/`; `public/` supplies
only supercell, seed, and the formal flag. `run_curie.py` writes every trajectory
to a `*.partial.traj` file, validates it against the exact frame/atom/finiteness
contract, then atomically renames it to its `.traj` name with a `.json` sidecar.
`checkpoint.json` records the run identity (profile/seed/structure/teacher) and a
hash of every committed artifact; a resume reuses only completed pairs whose
identity and hashes still match, never a partial trajectory, and otherwise fails
closed. For the paper profile the pilot must converge before production and all
13 production records must exist before `formal_result=true`.
`solution/curie_utils.py` is the pure (numpy + stdlib) contract layer,
unit-tested on the host in `test_analysis.py`. `solution/alt_curie.py` is an
independently-written solver (different unwrap, seed scheme, and layout) graded
by the same verifier under `smoke` — the G11 alternative-valid evidence.

## Restartable reference runner (`scripts/run_matclaw_reference_restartable.sh`)

The reference paper run is long (14 DP-MD trajectories: the 350 K pilot + 13
production temperatures, ~560k MD steps), so it does not fit in one
command-window or single-Slurm-job wall-time budget.  The runner therefore
**re-invokes the same solver entry point** (`/solution/solve.sh` →
`run_curie.py`) repeatedly until `checkpoint.json` reports `stage == "result"`.

Resume semantics live **inside `run_curie.py`**, not in the runner: on every
invocation `load_checkpoint` reuses ONLY completed trajectory pairs (final
`.traj` + `.json` sidecar) whose identity (profile/seed/structure/teacher) and
sha256 still match, and a torn `*.partial.traj` is *never* reused as a
completed record — it is discarded and that one temperature is re-run from the
locked initial structure + seed (`complete_trajectory` → `run_md`).  So each
invocation makes net forward progress: completed temperatures are skipped, the
first incomplete temperature is re-run from step 0, and the rest follow; no
`*.partial.traj` survives once the run reaches `stage == "result"` (the atomic
rename to the final name + sidecar promotes each trajectory on completion).

This is a **whole-temperature** resume, deliberately not a per-frame resume: a
single trajectory at the paper scale (max 50000 steps) fits well within one
invocation/wall-time budget, so a killed invocation costs at most re-running the
one trajectory it was mid-way through — never the full run.  Reconstructing
RNG/thermostat state mid-trajectory would risk a trajectory that is not
byte-identical to a continuous run, which the per-trajectory sha256 contract is
specifically designed to forbid; whole-temperature resume keeps every produced
trajectory a clean, from-seed, sha256-verifiable record.

The runner script is the **test-bed / persistent-container** form of the loop:
it `docker exec`s `solve.sh` until `checkpoint.json["stage"] == "result"`.  On
HPC the equivalent is a **controller resubmit loop** (`submit` → `wait_for` →
`fetch` → read `checkpoint.json["stage"]`; resubmit the same `remote_run_id`
until it is `"result"`); the controller is the delivery layer, the in-container
resume contract is identical.  The runner is reference-internal / diagnostic:
it is not part of the graded agent interface.

`test_restartable.py` is the dev-grade coverage for that resume contract — most
importantly that a 350 K trajectory interrupted at 1499/2501 frames is *never*
reused as a completed record and is re-run from the locked initial structure +
seed.  Like `test_analysis.py` it is dev-grade: it imports
`solution/curie_utils.py` and runs on the host (or in a container with
`/solution` mounted), and `test.sh` runs it only when
`/solution/curie_utils.py` is present so the graded reward gate stays green.
