Calculate the single-point energy of a water molecule using CP2K.

A template input file is provided at `/app/H2O_template.inp` with some placeholders marked as `FILL_ME`. Fill in the missing parts, then run CP2K and extract the total energy.

The missing parts are:
1. `FILL_ME_CELL` — the cell size (use a 15.0 Å cubic box)
2. `FILL_ME_COORD` — the water molecule coordinates (in Å):
   ```
   O  0.00000000  0.00000000  0.11926200
   H  0.00000000  0.76323900 -0.47704700
   H  0.00000000 -0.76323900 -0.47704700
   ```
3. `FILL_ME_XC` — the XC functional name (use `PBE`)

After filling the template:
1. Run: `/opt/cp2k/bin/cp2k.psmp -i /app/H2O.inp -o /app/H2O.out`
2. Extract the final total energy value (in Hartree) from the output (note: there may be multiple energy lines, make sure to get the final molecular energy, not the atomic guess energies)
3. Write ONLY the numeric value into `/app/out.txt`
