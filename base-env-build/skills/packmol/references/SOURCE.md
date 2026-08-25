# Source

## Primary sources

* Official user guide: https://m3g.github.io/packmol/userguide.shtml
* Official examples: https://m3g.github.io/packmol/examples.shtml
* Official utilities: https://m3g.github.io/packmol/utilities.shtml

## Purpose

These notes provide Packmol reference rules for zagent skill-guided, code-first molecular packing.

They are intended to support:

* Packmol input generation
* molecular mixture packing
* periodic bulk box construction
* electrolyte and salt-solution setup
* troubleshooting
* post-Packmol geometry checks
* downstream CP2K, LAMMPS, ASE, MLFF, DeepMD, and MACE workflows

## Scope

These notes summarize practical Packmol usage patterns for automated molecular packing workflows.

They focus on:

* `structure ... end structure` blocks
* molecule counts
* `tolerance`
* `filetype`
* `output`
* `inside box`
* `pbc`
* mixture packing
* density-based box-size estimation
* periodic minimum-image QC
* common Packmol failure modes

These notes do not replace the official Packmol documentation. If these notes conflict with the official Packmol user guide, the official user guide should be treated as the source of truth.

## Version note

Packmol behavior and supported command-line options may depend on the installed version.

When running in an automated workflow, record the Packmol version if available.

Recommended runtime metadata:

```text
packmol_version: <detected version or unknown>
packmol_executable: <path to executable>
packmol_input: <input filename>
packmol_output: <output filename>
```

The official documentation recommends using the latest Packmol version to ensure that all features are available.

## Access date

Accessed: 2026-07-14

## Citation

When Packmol is used in research output, cite the official Packmol reference recommended by the Packmol documentation.

Recommended citation:

```text
Martínez, L.; Andrade, R.; Birgin, E. G.; Martínez, J. M.
PACKMOL: A package for building initial configurations for molecular dynamics simulations.
Journal of Computational Chemistry, 2009, 30, 2157–2164.
```

## Agent-use policy

For zagent code-first workflows:

* generate Packmol inputs from explicit molecule counts and box dimensions
* use official Packmol syntax as the primary reference
* prefer deterministic input generation
* record molecule counts, component order, box dimensions, and density assumptions
* run Packmol through the available shell execution capability
* verify that Packmol reports success
* verify atom counts and coordinate-line counts
* for periodic systems, run PBC-aware minimum-image QC
* do not mark a structure as simulation-ready if Packmol failed or QC failed
* pass box dimensions explicitly to downstream simulation tools because ordinary XYZ output does not reliably preserve cell vectors

## Non-goals

These notes do not define:

* molecular force-field parameters
* atomic charges
* bonding topology
* CP2K basis sets or pseudopotentials
* LAMMPS pair styles
* MLFF training labels
* chemical equilibration criteria
* SCF convergence criteria
* AIMD production-readiness criteria
