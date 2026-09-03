# LC-MLP v1 descriptor recipe (candidate-facing)

This recipe restates the **published** descriptor and evaluation contract for
the LC-MLP v1 model-evaluation task. It defines outcomes, not an expert
directory layout: any implementation that produces the required descriptor
table, predictions, metrics, and manifest is acceptable.

## Descriptor basis

Each cluster is described by five fixed structural descriptors, computed with
a distance-cutoff radius of **6.0 angstrom**:

| descriptor | unit | meaning |
|---|---|---|
| `n_atoms` | – | cluster size |
| `r_min_ang` | angstrom | first-moment min pair distance |
| `r_max_ang` | angstrom | cutoff-window max pair distance |
| `coord_num_mean` | – | mean coordination at 6.0 angstrom |
| `e_distort` | – | distortion index (0 = icosahedral) |

## Evaluation table

Re-serialize the descriptor table for the evaluation clusters as a CSV with
the header

```text
cluster_id,n_atoms,r_min_ang,r_max_ang,coord_num_mean,e_distort,energy_per_atom_eV
```

For the Discovery smoke path the evaluation set is the 24-cluster calibration
table (`calibration-table-lj01-lj24`); the paper's held-out rows LJ-25..LJ-32
are not distributed and belong to the hidden verifier.

## Model execution contract

1. Standardize each of the five features with the published training-set
   mean/std (delivered with the released model configuration).
2. Apply the published LC-MLP v1 checkpoint (feed-forward network, two hidden
   layers of width 32, SiLU activations) to obtain `energy_per_atom_eV`
   predictions in eV/atom.
3. Compute energy MAE and RMSE in eV/atom against the evaluation-table labels.

## Reported band (acceptance basis)

The published study reports, on its fixed split:

- energy MAE <= 0.031 eV/atom
- energy RMSE <= 0.045 eV/atom

Hidden scientific scoring (the MLP-V4 layer) is deferred at Discovery MVP and
is stated abstractly in `instruction.md`; the values above are the published
reference band, not frozen case thresholds.
