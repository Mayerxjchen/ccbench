#!/bin/bash
sed \
  -e 's/FILL_ME_XC/PBE/' \
  -e 's/FILL_ME_CELL/15.0 15.0 15.0/' \
  -e '/FILL_ME_COORD/c\      O  0.00000000  0.00000000  0.11926200\n      H  0.00000000  0.76323900 -0.47704700\n      H  0.00000000 -0.76323900 -0.47704700' \
  /app/H2O_template.inp > /app/H2O.inp

/opt/cp2k/bin/cp2k.psmp -i /app/H2O.inp -o /app/H2O.out
grep "ENERGY| Total FORCE_EVAL" /app/H2O.out | awk '{print $NF}' > /app/out.txt
