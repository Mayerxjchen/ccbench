# O-gate reward-chain evidence (O1-O4)

Status: **in progress** (drafted while the smoke oracle runs).

## O1 test.sh exists + O2 verifier only reads /app
- tests/test.sh exists (031 pattern: runs `pytest /tests/test_outputs.py`, writes
  `reward.txt`). Marked true on the board.
- verifier.py reads only the submission root + staged `/tests/hidden`; no
  /reference /solution /private/tmp. Marked true on the board.

## O4 negative -> reward=0 (placeholder fixture, real path)
2026-08-10, ran the real eval.py-style staging+verify against
`tests/fixtures/good` (a structural-only fixture whose `final.pb` is a
34-byte placeholder — NOT deepmd-loadable):

```
PROFILE=smoke CASE=034 SUB=tests/fixtures/good bash reward_chain.sh
=> pytest: 3 failed, 10 passed (in 87.5s)
[034 test.sh] reward=0
```

The negative fixtures correctly FAIL the placeholder (L5 fails, tampered
copies fail), and the real path writes reward=0 to /logs/verifier/reward.txt.
This proves the O4 plumbing: an invalid/non-loadable submission -> reward=0
through the exact eval.py staging sequence (cp -a tests -> /tests -> test.sh
-> cat reward.txt).

A second O4 run against a **tampered copy of the real smoke oracle submission**
(corrupt model bytes + drop one raw AIMD output) will be appended here once
the oracle completes.

> **O4 tamper gotcha (fixed 2026-08-10):** the original tamper.sh used a
> generic `find "$OUT" -name '*pos-1.xyz' | head -1`, which hit the GEO_OPT
> file (`work/geopt/output/water64_geopt-pos-1.xyz`) because `work/geopt/`
> sorts before `work/aimd/`. Deleting the geopt pos file does NOT break L3
> (L3 reads the AIMD pos/frc siblings). Fixed to target
> `water64_aimd-pos-1.xyz` explicitly AND the `final/provenance/aimd-raw/`
> copy, so no complete AIMD trajectory survives the tamper.

## O3 clean oracle -> reward=1 (pending paper-profile run)
The smoke oracle (AI2KIT_PROFILE=smoke, container 034-oracle-smoke) is a
**harness proof** of the workflow: GEO_OPT -> AIMD -> AL -> validation execute
end-to-end. Its model is trained at harness scale (~100 AIMD steps, 2000 train
steps, 2 AL rounds), which is NOT expected to meet the DRAFT L7 hidden E/F bar
(force RMSE <= 0.06 eV/A, centered on the expert's 0.04288). thresholds.json
explicitly says "do NOT freeze these now".

**Verifier profile-gate (tests/verifier.py:1268) confirmed:** under
`PROFILE=paper`, a manifest whose `profile` is `smoke` is a HARD reject
(reward=0, "submission is a smoke-profile run; cannot satisfy formal
grading"). This gives TWO honest O3 measurements against the smoke oracle:
1. `PROFILE=paper`  -> immediate profile-gate reject (proves the gate fires)
2. `PROFILE=smoke`  -> full L1-L9 pass through the real eval path, with L7
   (and possibly L9) expected to fail at smoke scale -> reward=0

Therefore the honest expectation for the smoke submission is reward=0 through
the real path. That is real evidence the reward chain is not a rubber stamp,
but O3's "clean oracle -> reward=1" box requires the **paper-profile** oracle
(AIMD 5000, train 40000, 3 AL rounds) whose model passes L7-L9. O3 stays open
until that run. Both measurements above will be recorded here once the smoke
oracle completes.

## Re-runnable
```
CASE=/path/to/034 SUB=<submission> PROFILE=smoke|paper bash /tmp/034-rewardchain/reward_chain.sh
```

## I3 overlap-check tool (2026-08-10)
`/tmp/034-rewardchain/i3_overlap_check.py` computes (hidden frame coords
sha256@prec3) ∩ (all DeepMD set.NNN/coord.npy + new-dataset frames under the
submission). Validated on `tests/fixtures/good`: it correctly reports
60/72 training frames overlapping the hidden set (exit 1). This is the
reusable gate for I3. Expected to PASS (exit 0) on the smoke oracle once its
AL loop finishes (fresh AIMD trajectory != expert frames).

## Hidden-set independence caveat (audit 2026-08-10, linked to I-gate)
Independent audit + re-verify found `tests/hidden/dft-validation.extxyz`
sha256 == `reference/expert-trajectory/aimd/processed/aimd.xyz` (both
`1b63da0c...`); the generator copies the expert mother set verbatim
(`generate_hidden_validation.py:128` shutil.copyfile). Consequences:
- **Safe** for any agent that re-runs its own CP2K AIMD (the oracle does) —
  a fresh NVT trajectory does not byte-match the expert frames, so L7 is a
  genuine held-out score. The smoke oracle will prove this via an I3
  train/hidden hash-intersection check once its AL loop finishes.
- **Not hidden** against a submission that copies the reference trajectory
  verbatim as its "training data". The agent image excludes reference/
  (B2/B3), but this is an integrity caveat, not an architectural guarantee.
- The verifier has NO I3 (train/hidden overlap) check; VALIDATION.json I1 has
  been re-opened pending the smoke oracle's non-overlap proof.
The verifier's L8 runs its own short NVT via `lmp -i nvt.in` with `pair_style
deepmd`. In `dftworld-base-ai2kit:0.1.0-cpu` this works:
- `LAMMPS_PLUGIN_PATH=/opt/ai2kit/lib/python3.11/site-packages/deepmd/lib`
  (image already exports it); `plugin load` -> "Loaded 1 plugins" +
  "Summary of lammps deepmd module" with the correct pair style registered.
- The `DeePMD-kit: Cannot find libcudart.so.12` line is a HARMLESS CPU-only
  warning (the CPU build falls back); the only real error seen was an
  incompatible placeholder `.pb` (version 0.0), which a genuine model avoids.
- So the reward-chain container can run the full L1-L9 pytest suite including
  L8's NVT. Any O3/O4 reward that comes out of it is a real measurement.

## Verifier self-test (2026-08-11) — grader reliability check

Ran the real `test.sh -> verifier -> reward.txt` path on the **completed smoke oracle**
(ORACLE_EXIT=0, full GEO_OPT->AIMD->AL->validation->final). Findings:

**Grader correctly rejects a scientifically-weak model:**
- L1-L6 PASS, L7/L8/L9 FAIL on the smoke submission -> valid=False -> reward=0. Honest.
- 8/8 negative tests correctly fail: corrupt model (L5), no-AIMD (L3), nonphysical
  energies (L3), no-iterative-improvement (L6), bad hidden E/F (L7), unstable NVT (L8),
  shifted RDF (L9), rdf-reference self-consistent.
- PROFILE=paper on a smoke manifest: profile-gate fires cleanly (valid=False,
  "cannot satisfy formal grading"). reward=0.

**3 negative fixtures are TOO WEAK (false negatives) — fixture bugs, not verifier bugs:**
1. test_manifest_only_no_model_fails: deletes only `models/final/compress.pb`; the
   `final/models/final/compress.pb` copy survives -> L5 still resolves. Fix: delete ALL
   resolvable model paths (find_model_files checks root/rel AND root/final/rel).
2. test_forged_labels_fail: forges only set_dirs[0] (dp-test held-out set, sorts first)
   with 2.0 A noise; L3's 40-frame trace sample still >=90% match. Fix: forge ALL
   training sets + stronger noise, or assert trace_pct < 90.
3. test_dataset_does_not_grow_fails: base frame taken from dp-test set (sorts first);
   those frames still trace to CP2K so L6 sees them as "new configs". Fix: base from the
   AIMD set (in aimd_set -> skipped -> new_positions=0).

**alt-valid + oracle-workspace need a scientifically-adequate model:**
- test_alt_valid_renamed_rounds_still_passes FAILS on smoke because the smoke model
  fails L7-L9 regardless of round naming. Requires the formal expert reference rerun
  (paper-scale model) to demonstrate "renaming doesn't break validity".
- Conclusion: grader core is sound; the negative fixtures must be hardened AND a
  paper-scale reference submission must pass before the grader is proven reliable.

## 2026-08-11 follow-up — fixture hardening verified + L6 loophole closed

**3 hardened negative fixtures now verified in the container** (test_outputs.py,
`dftworld-base-ai2kit:0.1.0-slurmfix`, smoke profile, on /tmp/034-app):
- `test_manifest_only_no_model_fails` -> correctly rejects (deletes every
  resolvable model path: root/rel + root/final/rel + models/final/*.pb).
- `test_forged_labels_fail` -> correctly rejects (forges ALL set.npy AND labeled
  extxyz with 5.0 A noise; L3 sampled trace drops < 90%).
- `test_dataset_does_not_grow_fails` -> correctly rejects (overwrites EVERY
  training-frame source — set.npy AND labeled extxyz — with AIMD frame-0, so
  L6 finds ZERO new labeled configs).
All three went from false-negative (valid=True, fixture too weak) to genuine
rejections (valid=False, L3/L5/L6 not ok).

**L6 round-counting loophole found + fixed (verifier.py `find_al_artifacts`):**
ai2-kit writes a committee of N models under `iter-NNN/deepmd/model-0..N`; each
model dir holds `input.json` + a graph, so `find_al_artifacts` counted N model
dirs as N "rounds". A submission that trains ONE committee and never re-trains
looked like N rounds and passed L6's `rounds >= min_rounds+1` gate. Confirmed on
the smoke submission: rounds=2 before the fix, rounds=**1** after deduping by the
committee's parent dir. The smoke submission now honestly FAILS L6 (1 round, no
new-dataset, no iter-002, no retrain). The reference-runs (AIMD500 candidate,
AL_ROUNDS=2) must show iter-001 + iter-002 + new-dataset + real CP2K labels.

**K2 evidence collected on the smoke submission** (`/tmp/034-k2-evidence/`):
K2a slurm-command provenance (14 lines), K2b dp-train logs (18), K2c model_devi
artifacts (7), K2e CP2K label job outputs (6), K2f env resolution (1) all present.
**K2d is EMPTY** — the smoke AL produced NO `iter-*/new-dataset` trees: the AL
iteration's data-conversion + retrain step never completed (smoke restart residue).
This is a smoke-harness limitation, documented honestly; K2d is a HARD requirement
for the reference-runs and any formal submission.

**OOM constraint recorded:** re-running the FULL pytest suite (L7/L8/L9 heavy
inference) WHILE reference-run-01 (AIMD500, CP2K GEO_OPT/AIMD) is active KILLS
the suite (exit 137, 17GB Mac). Heavy grader tests are deferred until the
reference run reaches a low-compute phase or completes. A lightweight
`find_al_artifacts` check (pure file IO) ran fine alongside it and confirmed
rounds=1.

**Remaining before O-gate closes:** O3 paper-grade reference (reference-run-01/02)
passes L1-L9 -> reward=1; O4 tampered-copy reward=0; I3 hidden-vs-training overlap
=0; K2d non-empty on the reference runs.
