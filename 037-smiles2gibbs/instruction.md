Given the SMILES string `CC=O` (acetaldehyde), compute its Gibbs free energy at 298.15 K and 1 atm using GFN2-xTB, and write it (in eV) as JSON to `/app/out.json`, e.g. `{"gibbs_free_energy_eV": -123.45}`.

Optimize the geometry first (RDKit embed with `randomSeed=42`, then GFN2-xTB `BFGS` to `fmax=0.02` eV/Å), run a vibrational analysis (`ase.vibrations.Vibrations`) at that geometry to get the vibrational energies, then feed the electronic energy and vibrational energies into `ase.thermochemistry.IdealGasThermo` with `geometry="nonlinear"`, `symmetrynumber=1`, `spin=0`.
