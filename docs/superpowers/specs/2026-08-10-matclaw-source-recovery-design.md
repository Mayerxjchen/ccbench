# MatClaw CIPS Source Recovery Design

## Goal

Recover and lock the public scientific sources needed for MatClaw-derived cases
031–033 without presenting paper reconstruction, remote-path references, or missing
binary outputs as upstream artifacts.

## Scope

This phase creates only a shared source package under
`benchmark/sources/matclaw/`. It does not create case directories, Dockerfiles,
base images, regenerated references, evaluators, or No-Skill/With-Skill runs.

The target cases remain:

- `031-matclaw-cips-active-distillation`
- `032-matclaw-cips-curie-temperature`
- `033-matclaw-cips-domain-wall-search`

## Authoritative Sources

### MatClaw paper

Use arXiv `2604.02688v3`, dated 2026-05-22. Store the PDF byte-for-byte and lock
its SHA-256. Paper-derived facts are evidence, but files recreated from those facts
must be marked `reconstructed_from_paper`.

### MatClaw repository

Use `https://github.com/cz2014/MatClaw.git` at these immutable commits:

- `public`: `cebcf2be839af87663c0e5b64efaf1afe99f2e39`
- `release`: `52557c077f5e3be8444a3f03ea10a647fbd442ca`

The `public` branch contains the current released application but not the CIPS
demonstration workspaces. The older `release` branch is the upstream source for:

- `.ref/CuInP2S6.cif`
- `.ref/cips_monolayer.cif`
- `.ref/cips_monolayer.data`
- `workspace_demo1a_distill`
- `workspace_demo1b_distill_pdf`
- `workspace_demo2a_curie_no_convergence`
- `workspace_demo2b_curie_with_convergence`
- `workspace_demo3_search`

Every copied repository file records its repository path, branch, commit, SHA-256,
size, and provenance `upstream_repository`.

### He et al. teacher source

Use He et al., *Physical Review B* 108, 024305 (2023), DOI
`10.1103/PhysRevB.108.024305`, and its cited AIS Square record as the authoritative
source for the CIPS DeePMD model and associated inputs. A model recovered from any
mirror is accepted only if its identity can be tied to that record and its hash is
locked.

The MatClaw repository does not contain `frozen_model.pb`; its workspaces reference
the inaccessible HPC path
`/pscratch/sd/c/cz2014/cips_distill/frozen_model.pb`. A path reference is not a
recovered model.

## Recovery Layout

```text
benchmark/sources/matclaw/
├── README.md
├── source.lock.json
├── recovery_gate.json
├── inventory.json
├── paper/
│   ├── matclaw-2604.02688v3.pdf
│   └── he-physrevb-108-024305.pdf
├── repository/
│   └── release/
├── common/
│   ├── CuInP2S6.cif
│   ├── cips_monolayer.cif
│   ├── cips_monolayer.data
│   └── teacher-model/
├── task1/
├── task2/
└── task3/
```

`repository/release/` contains the selected upstream workspace snapshot. The
`common/` and `task*` paths contain only explicitly catalogued files copied from
that snapshot or separately recovered authoritative sources. Large unrelated
MatClaw code and RAG corpora are not vendored.

## Provenance Contract

Every inventory entry uses one of these values:

- `upstream_repository`
- `upstream_paper`
- `upstream_author_dataset`
- `reconstructed_from_paper`
- `missing_upstream`

Each non-missing file entry contains its SHA-256, byte size, source URL or repository
path, and immutable source revision. Reconstructed files live separately from raw
upstream snapshots and cannot satisfy an upstream-recovery gate.

No local absolute HPC path, job UUID, report statement, or figure is treated as
proof that an omitted binary or trajectory was recovered.

## Task 3 Electric-Field Protocol

The `release` workspace history is sufficient to recover the upstream protocol:

- the MD calculator is `SumCalculator([DeePMD, UniformElectricForce])`;
- atom `i` receives `F_DP(i) + q_i E`;
- the external-field energy is `-sum(q_i * r_i dot E)`;
- effective charges are Cu `+0.765 e` and In/P/S `-0.085 e`;
- the field is expressed in `eV/Angstrom/e`, numerically equivalent to `V/Angstrom`.

The recovery package stores the relevant raw history plus a machine-readable protocol
record that cites exact source lines or history steps. This counts as verified protocol
provenance, but it does not substitute for the missing teacher model or trajectories.

## Recovery Gate

`recovery_gate.json` contains at least:

```json
{
  "structure_recovered": true,
  "teacher_model_recovered": false,
  "task1_workspace_recovered": true,
  "task2_workspace_recovered": true,
  "task3_workspace_recovered": true,
  "task3_electric_field_protocol_verified": true,
  "task2_raw_trajectories_recovered": false,
  "task3_raw_trajectories_recovered": false,
  "repository_commit_pinned": true,
  "source_recovery_complete": false,
  "benchmark_construction_unblocked": false
}
```

Values are derived from the inventory rather than manually asserted. The expected
initial state above reflects the currently observed repository. It changes only when
new files are actually recovered and hashed.

## Validation

Source recovery is validated by automated checks that:

1. verify every recorded SHA-256 and byte size;
2. verify both repository commit identifiers;
3. prove required workspace directories and structure files are present;
4. prove no `*.pb`, `*.pth`, or equivalent teacher binary is falsely claimed from a
   remote-path reference;
5. verify the electric-field protocol record against the upstream history;
6. reject unknown provenance values and missing source locators;
7. recompute the gate from the inventory and compare it with `recovery_gate.json`;
8. confirm that no `031-*`, `032-*`, `033-*`, or MatClaw Dockerfile was created.

## Stop Conditions

Source recovery stops visibly, without beginning benchmark construction, when:

- the teacher model cannot be downloaded from an authoritative public source;
- a recovered model cannot be loaded by a compatible open-source DeePMD version;
- its element/type-map identity cannot be verified;
- an artifact's provenance cannot be distinguished from paper reconstruction; or
- a required raw source is available only through inaccessible author HPC storage.

## Success Criteria

This phase succeeds as an audit when the source package is internally consistent and
truthfully records all recovered and missing material. It unblocks shared-image and
case construction only when the teacher model is recovered, hashed, load-tested, and
the recovery gate sets `benchmark_construction_unblocked` to `true`.
