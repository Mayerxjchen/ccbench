"""Recompute the ground-truth xTB-optimized geometry for 038-smiles2file.

Run inside dftworld-base-xtb:
  docker run --rm -v $(pwd):/work -w /work dftworld-base-xtb \
    bash -c "source /app/.venv/bin/activate && python3 generate_reference.py"
"""
import importlib.metadata
import json
from pathlib import Path

import ase
import rdkit
from ase import Atoms
from ase.optimize import BFGS
from rdkit import Chem
from rdkit.Chem import AllChem
from tblite.ase import TBLite

smi = "CC(=O)C"
mol = Chem.MolFromSmiles(smi)
mol = Chem.AddHs(mol)
AllChem.EmbedMolecule(mol, randomSeed=42)
AllChem.MMFFOptimizeMolecule(mol)
conf = mol.GetConformer()
symbols = [a.GetSymbol() for a in mol.GetAtoms()]
positions = conf.GetPositions()

atoms = Atoms(symbols=symbols, positions=positions)
atoms.calc = TBLite(method="GFN2-xTB", verbosity=0)
BFGS(atoms, logfile=None).run(fmax=0.02)

reference = {
    "molecule_name": "acetone",
    "input_smiles": smi,
    "n_atoms": len(atoms),
    "symbols": atoms.get_chemical_symbols(),
    "embed_seed": 42,
    "fmax": 0.02,
    "final_positions_angstrom": atoms.get_positions().tolist(),
    "final_energy_eV": float(atoms.get_potential_energy()),
    "output_filename": "acetone.xyz",
    "rdkit_version": rdkit.__version__,
    "ase_version": ase.__version__,
    "tblite_version": importlib.metadata.version("tblite"),
}
(Path(__file__).parent / "reference.json").write_text(json.dumps(reference, indent=2) + "\n")
print(json.dumps({k: v for k, v in reference.items() if "positions" not in k}, indent=2))
