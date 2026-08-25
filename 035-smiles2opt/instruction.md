Given the SMILES string `OC1=CC=CC=C1` (phenol), optimize its molecular geometry and save the final structure to `/app/out.xyz` in standard XYZ format.

Build the initial 3D structure with RDKit (`AllChem.EmbedMolecule(mol, randomSeed=42)` + `AllChem.MMFFOptimizeMolecule`), then optimize it with ASE using the GFN2-xTB calculator (`tblite.ase.TBLite(method="GFN2-xTB")`) and a force convergence of `fmax=0.02` eV/Å.
