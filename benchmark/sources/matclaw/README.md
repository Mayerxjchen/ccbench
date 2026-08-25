# MatClaw CIPS Source Recovery

Core source recovery: COMPLETE

Benchmark construction: UNBLOCKED

This directory is the provenance package for the proposed MatClaw CIPS cases.
It does not create cases 031–033, a Dockerfile, or a benchmark image.

## Recovery status

| Artifact | Status | Evidence |
| --- | --- | --- |
| MatClaw paper | recovered | arXiv `2604.02688v3`, exact PDF bytes |
| MatClaw repository | recovered | `release@52557c077f5e3be8444a3f03ea10a647fbd442ca`; `public@cebcf2be839af87663c0e5b64efaf1afe99f2e39` recorded in `source.lock.json` |
| CIPS structures | recovered | byte-identical `.ref` copies; pymatgen/ASE audit |
| Teacher model | recovered | AIS Square record 109, `vdW_CuInP2S6_optB86b` |
| Teacher type map | verified | `Cu In P S` |
| Teacher load test | passed | DeePMD-kit 2.2.11 + TensorFlow 2.16.2; finite 10-atom inference |
| Task 1 workspace | recovered | demos 1a and 1b, every file hashed in `task1/manifest.json` |
| Task 2 workspace | recovered | demos 2a and 2b, every file hashed in `task2/manifest.json` |
| Task 3 workspace | recovered | demo 3, every file hashed in `task3/manifest.json` |
| Task 3 electric-field protocol | verified | pinned implementation source plus history JSONL lines 1, 2, 10, and 11 |
| Task 2 raw trajectories | not recovered | manifest names and remote job UUIDs exist; `.traj` files are absent upstream |
| Task 3 raw trajectories | not recovered | 14 UUIDs and `/pscratch` paths exist; `.traj` files are absent upstream |

The two missing trajectory rows do not change the core source gate: the official
workspaces, derived results, authoritative teacher model, structure, and Task 3
field implementation are recovered. They do mean that later regenerated
references must run the simulations locally and must not label regenerated
trajectories as upstream files.

## Teacher-model provenance

He et al., *Physical Review B* **108**, 024305 (2023), reference 39 points to
AIS Square model `vdW_CuInP2S6_optB86b`. The public AIS Square search API resolves
that record to ID 109 and the author-published `CIPS_data.zip`. The original
archive is retained, and `frozen_model.pb` plus `type_map.raw` are extracted
without transformation. `common/teacher-model/recovery.json` records URLs,
authors, sizes, hashes, archive members, and load-test evidence. The public API
did not expose a license value, which is recorded rather than inferred.

## Electric-field protocol

The pinned release implementation in `remote_jobs/_efield_calculator.py` returns
`SumCalculator([DeePMD, UniformElectricForce])`, with total force
`F_DP(i) + q_i E` and external energy `-sum_i(q_i r_i · E)`. Charges are Cu
`+0.765 e` and In/P/S `-0.085 e`; `eV/angstrom/e` is equivalent to
`V/angstrom`. See `task3/electric-field-protocol.json` for exact evidence
locations and the history hash.

## Reproduction

The complete clean-room sequence runs from the repository root. Replace
`/tmp/matclaw-recovered` only with a fresh destination:

```bash
uv run python -m benchmark.sources.matclaw.recover_sources --output /tmp/matclaw-recovered
uv run python -m benchmark.sources.matclaw.recover_teacher --download-archive /tmp/CIPS_data.zip
uv run --with pymatgen --with ase python -m benchmark.sources.matclaw.audit_structures --root /tmp/matclaw-recovered
uv run python -m benchmark.sources.matclaw.recover_teacher --root /tmp/matclaw-recovered --archive /tmp/CIPS_data.zip --prepare
uv run --no-project --isolated --python 3.11.15 --with-requirements /tmp/matclaw-recovered/teacher-runtime-requirements.txt python benchmark/sources/matclaw/validate_teacher_model.py --model /tmp/matclaw-recovered/common/teacher-model/frozen_model.pb --structure /tmp/matclaw-recovered/common/CuInP2S6.cif --type-map /tmp/matclaw-recovered/common/teacher-model/type_map.raw --output /tmp/matclaw-load-test.json
uv run python -m benchmark.sources.matclaw.recover_teacher --root /tmp/matclaw-recovered --archive /tmp/CIPS_data.zip --load-test /tmp/matclaw-load-test.json
uv run python -m benchmark.sources.matclaw.build_task_views --root /tmp/matclaw-recovered
uv run python -m benchmark.sources.matclaw.recovery --root /tmp/matclaw-recovered --write
uv run pytest tests/test_matclaw_source_recovery.py -q
```

The paper and AIS archive downloads are rejected unless their SHA-256 values
match the locks. The legacy TensorFlow model load test is intentionally isolated;
`teacher-runtime.lock.json` pins CPython 3.11.15, macOS arm64, and the SHA-256 of
the complete 55-distribution requirements lock. Its output is bound to the model,
structure, and type-map hashes before recovery is accepted.
