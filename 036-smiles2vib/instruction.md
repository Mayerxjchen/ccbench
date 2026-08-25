Given the SMILES string `C=O` (formaldehyde), compute its vibrational frequencies using GFN2-xTB and report them, in cm⁻¹, as a JSON list to `/app/out.json`, sorted from lowest to highest.

First optimize the geometry (RDKit embed with `randomSeed=42`, then GFN2-xTB `BFGS` to `fmax=0.02` eV/Å), then run a numerical Hessian (`ase.vibrations.Vibrations`) at that optimized geometry and keep only the genuine vibrational modes — discard the near-zero translational/rotational modes (3N-6 = 6 real modes for formaldehyde).
