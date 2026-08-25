Given the SMILES string `CC(C)CC1=CC=C(C=C1)C(C)C(=O)O` (ibuprofen), generate its 3D atomic coordinates and save them to `/app/out.xyz` in standard XYZ format.

Embed the 3D conformer directly from the SMILES with RDKit (`AllChem.EmbedMolecule(mol, randomSeed=42)`) — do not run any further geometry optimization.
