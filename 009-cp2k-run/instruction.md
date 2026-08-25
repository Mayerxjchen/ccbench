A CP2K input file for a water molecule single-point energy calculation is provided at `/app/H2O.inp`.

Run the calculation:
```
/opt/cp2k/bin/cp2k.psmp -i /app/H2O.inp -o /app/H2O.out
```

Then extract the final total energy from the output file (note: there may be multiple energy lines, make sure to get the final molecular energy, not the atomic guess energies) and write ONLY the numeric value (in Hartree) into `/app/out.txt`.
