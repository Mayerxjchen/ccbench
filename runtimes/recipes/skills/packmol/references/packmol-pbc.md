# Packmol periodic-boundary reference

Use Packmol 21.x periodic syntax with an orthorhombic cell:

```text
tolerance 2.0
filetype xyz
output packed.xyz
pbc 0.0 0.0 0.0 Lx Ly Lz

structure component.xyz
  number N
  inside box 0.0 0.0 0.0 Lx Ly Lz
end structure
```

Use the same bounds in `pbc` and `inside box`. For a cube,
`Lx = Ly = Lz = L`. Keep this explicit even when the box was derived from
density.

Packmol places each structure as a rigid whole and handles periodic images.
Do not pre-wrap the atoms of a molecule independently: that can split its
geometry. A packed molecule can cross a boundary and consequently have raw XYZ
coordinates slightly outside `[0,L)`. This is not itself an error. Preserve the
box separately because ordinary XYZ does not store it, and pass the cell and
PBC flag explicitly to downstream ASE, LAMMPS, CP2K, or data-conversion steps.

For non-periodic packing, omit `pbc` and use the requested finite region. Do not
silently convert between periodic and non-periodic modes.
