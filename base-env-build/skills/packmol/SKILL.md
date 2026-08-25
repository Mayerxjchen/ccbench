---
name: packmol
description: >
  Generate and validate initial Packmol configurations from specified molecule
  or ion structures, counts, target density or box dimensions. Use when a user
  needs an auditable packed starting geometry for molecular simulation.
compatibility: Requires Python 3 and a Packmol executable in the execution environment.
license: LGPL-3.0-or-later
metadata:
  version: "4.0"
  upstream_repository: https://github.com/m3g/packmol
  requires:
    os: [linux, darwin]
    requires:
      bins: [python3]
---

# Packmol Generate

Generate an auditable initial molecular configuration. Packmol produces initial
packing, not equilibration; ordinary XYZ does not preserve the cell, topology,
charges, force-field parameters, or DFT settings.

## Execution principle

Use registered generic tools to call the bundled scripts under
`{baseDir}/scripts/`. Here `{baseDir}` is the directory containing this
`SKILL.md`. Do not rewrite their validation logic. Preserve the task manifest,
component XYZ files, Packmol input, log, packed XYZ, and QC output.

## Reference routing

Read references only when their condition applies:

- For periodic packing, read `{baseDir}/references/packmol-pbc.md` before
  generating Packmol input and read
  `{baseDir}/references/pbc-qc-pattern.md` before QC.
- For mixtures, electrolytes, salts, solvents, or ions, read
  `{baseDir}/references/packmol-mixture.md` before resolving composition.
- Before interpreting Packmol version, success markers, or constraint
  violations, read `{baseDir}/references/packmol-official.md`.
- After a failed, timed-out, hanging, empty, or overlapping result, read
  `{baseDir}/references/packmol-troubleshooting.md` before one controlled
  retry.

## Procedure

### 1. Resolve scientific inputs

Collect component identity/count, one single-molecule or ion XYZ per component,
formal charges for ionic components, target density **or** fixed dimensions,
periodic versus non-periodic mode, tolerance, paths, and system name. Do not
invent or silently default scientific values. Ask for missing facts; if
automation cannot ask, stop with an exact missing-input report.

### 2. Write the Task Manifest

Create `packmol-task.json` from this exact schema, replacing example values:

```json
{
  "schema_version": 1,
  "system_name": "system",
  "components": [{
    "name": "H2O",
    "template_path": "~/water.xyz",
    "template_origin": "provided",
    "count": 64,
    "formal_charge_e": 0,
    "molar_mass_g_mol": null
  }],
  "box": {
    "periodic": true,
    "dimensions_A": null,
    "target_density_g_cm3": 1.0
  },
  "packmol": {
    "tolerance_A": 2.0,
    "seed": null,
    "input_path": "~/system.inp",
    "log_path": "~/packmol.out",
    "output_path": "~/system.xyz"
  },
  "provenance": {
    "confirmed_fields": [],
    "defaulted_fields": []
  }
}
```

`template_origin` is `provided`, `existing`, or `generated`. Record every
scientific field under `confirmed_fields` or `defaulted_fields`; defaults must
be explicitly accepted. Create a template only when identity and geometry are
unambiguous. Generated multi-atom templates require an explicitly supported
validator; otherwise stop.

Specify exactly one of `dimensions_A` and `target_density_g_cm3`. For a
density-derived cubic box, validation uses `M_sum = Σ(N_i M_i)`,
`V_A3 = (M_sum / N_A / rho) × 10^24`, and `L_A = V_A3^(1/3)`, with
`N_A = 6.02214076 × 10^23 mol^-1`. Never use solvent mass alone. A non-neutral
system requires an explicitly confirmed net-charge policy.

### 3. Run preflight and input validation

Call `preflight.py --workdir <dir>`, then call
`validate_manifest.py <task.json> --output <normalized.json>`. Continue only
when both commands exit zero and emit PASSED `JSON result` records
named `packmol_preflight` and `packmol_inputs`. Do not install missing software
or repair invalid chemistry by guessing.

### 4. Generate and execute Packmol

Call `build_packmol_input.py <normalized.json>` and require
`packmol_input_generated`. Execute Packmol with explicit input redirection,
combined log capture, timeout, and exit-code preservation. Keep failed attempts.
For periodic systems use the loaded PBC rules; for non-periodic work omit PBC.
Tool Result failures must trigger troubleshooting guidance before a controlled
retry.

### 5. Validate, QC, and deliver

Call `validate_packmol_result.py <normalized.json> --exit-code <code>` and
require `packmol_execution`. Then call `qc_structure.py <normalized.json>` and
require `packmol_structure_qc`. Report the minimum-image intermolecular result,
gross-overlap verdict, independent tolerance compliance, closest pair, atom
count, composition, charge, box, density, software version, and source paths.

Return the manifest, templates, input, log, packed XYZ, and QC report. Set
`preparation_stage` to `"packed"`; the output is not simulation-ready or
production-ready and still needs cell/topology assignment, minimization, and
equilibration.

## Completion criteria

All five named result records are required. A WARNING QC may complete with
warnings. Missing results yields `incomplete`; FAILED execution or QC yields
`failed`. Never replace missing JSON results with prose.
