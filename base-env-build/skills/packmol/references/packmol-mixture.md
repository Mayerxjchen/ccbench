# Molecular-mixture and electrolyte reference

Treat every solvent molecule, cation, anion, and neutral solute as a distinct
Packmol component with its own single-component XYZ and `structure` block.
Preserve the requested counts exactly.

Record each component's `template_origin` as `provided`, `existing`, or
`generated`. Complex molecules and polyatomic ions such as EC, DMC, PF6, or
TFSI remain supported when a valid provided/existing XYZ is available. Do not
generate such multi-atom structures from a name alone; generated geometry is
accepted only for explicitly validated simple species.

## Preflight checks

- Verify each component XYZ independently and compute its atom count and mass.
- For density calculations use `M_sum = Σ(N_i M_i)` across all components;
  never compute the box from solvent mass alone.
- Compute expected total atoms as `Σ(N_i A_i)` before running Packmol.
- Check formal neutrality with `Σ(N_i q_i) = 0`, unless a charged cell was
  explicitly requested. XYZ labels do not assign formal charges.
- Preserve a component manifest containing name, source/template path, count,
  atoms per component, mass, and formal charge used for the check.

Example ordering:

```text
structure water.xyz
  number 96
  inside box 0.0 0.0 0.0 L L L
end structure
structure sodium.xyz
  number 4
  inside box 0.0 0.0 0.0 L L L
end structure
structure chloride.xyz
  number 4
  inside box 0.0 0.0 0.0 L L L
end structure
```

Packmol output follows component-block order and repeats each template's atom
order. Use that deterministic ordering to reconstruct molecule groups during
QC. Record the order; do not infer molecule identity only from atom names when
multiple components share elements.

For electrolyte production work, the packed coordinates still need topology,
charge/force-field assignment, cell metadata, minimization, and equilibration.
