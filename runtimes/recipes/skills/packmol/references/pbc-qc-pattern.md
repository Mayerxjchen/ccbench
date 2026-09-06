# Periodic minimum-image QC reference

QC must measure contacts between different molecules/ions, not covalent bonds
inside one template.

## Reconstruct molecule groups

Use the Packmol component-block order. For component `i` with `N_i` copies and
`A_i` atoms per copy, consume `N_i × A_i` consecutive output atoms and assign
each `A_i`-atom chunk one molecule ID. Single-atom ions are one molecule each.
Confirm that the final cursor equals the XYZ atom count.

## Minimum-image distance

For an orthorhombic box with lengths `L = (Lx,Ly,Lz)`, apply

```python
delta = xyz[i] - xyz[j]
delta -= box * np.round(delta / box)
distance = np.linalg.norm(delta)
```

only when molecule IDs differ. Track the global minimum and the associated
atom indices, elements, component names, and molecule IDs. A block-wise or
chunked implementation is preferable for large systems to avoid allocating an
`N × N × 3` array.

## Two independent verdicts

Generic first-pass heuristic for gross intermolecular overlap:

| Minimum distance | Verdict |
|---|---|
| `< 1.2 Å` | `FAILED` |
| `1.2–1.8 Å` | `WARNING` |
| `≥ 1.8 Å` | `PASS` |

This is not an element-pair physical criterion. Close H-bond or ion-solvent
contacts may require scientific inspection. PASS means only that this generic
screen found no obvious severe overlap.

Separately test the requested Packmol tolerance:

```text
tolerance_compliant = d_min >= tolerance_A - 0.01 Å
```

The 0.01 Å allowance is numerical/reporting slack, not permission to reduce
the requested tolerance. A generic PASS can coexist with tolerance failure;
report both and use the more conservative status for handoff.

## Required QC report

Include expected/actual atoms, component counts, box lengths, grouping method,
minimum-image distance, closest pair details, generic verdict, requested
tolerance, tolerance-compliance verdict, and limitations. Coordinates slightly
outside `[0,L)` are acceptable when an intact molecule straddles PBC; never
atom-wrap before measuring or downstream conversion.
