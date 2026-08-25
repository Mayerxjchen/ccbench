Calculate the Van der Waals volume of caffeine (SMILES: `CN1C=NC2=C1C(=O)N(C(=O)N2C)C`) using RDKit.

Steps:
1. Activate the virtual environment: `source /app/.venv/bin/activate`
2. Use RDKit to create the molecule from SMILES, add hydrogens, and generate 3D coordinates with `randomSeed=42`
3. Optimize the geometry with `MMFFOptimizeMolecule`
4. Calculate the VDW volume using a grid-based method with spacing=0.25 and standard VDW radii (H=1.20, C=1.70, N=1.55, O=1.52)
5. Write ONLY the numeric value (in Å³, 2 decimal places) into `/app/out.txt`

The result should be around 164-165 Å³.
