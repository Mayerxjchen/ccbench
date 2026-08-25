Calculate the single-point energy of a water molecule using CP2K.

The water molecule coordinates (in Angstrom) are:
```
O  0.00000000  0.00000000  0.11926200
H  0.00000000  0.76323900 -0.47704700
H  0.00000000 -0.76323900 -0.47704700
```

Requirements:
- Use DFT with the PBE functional and GPW method
- Use the `DZVP-MOLOPT-SR-GTH` basis set and `GTH-PBE` pseudopotentials
- This is an isolated molecule (non-periodic), set up a 15 Angstrom cubic cell with `PERIODIC NONE`
- Include the `&POISSON` block with `PERIODIC NONE` and `POISSON_SOLVER MT`
- Extract the total energy in Hartree and write ONLY the numeric value into `/app/out.txt`

The CP2K binary is located at `/opt/cp2k/bin/cp2k.psmp`.
Basis set file: `BASIS_MOLOPT`, Potential file: `GTH_POTENTIALS`.
