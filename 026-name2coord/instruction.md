Generate the 3D atomic coordinates of ethanol and save them to `/app/out.xyz` in standard XYZ format.

A name→SMILES lookup table is available at `/app/name_lookup.json`. Embed the 3D conformer directly from the SMILES with RDKit (`AllChem.EmbedMolecule(mol, randomSeed=42)`) — do not run any further geometry optimization.
