#!/bin/bash
# Oracle solution —— 与 reference/reference.json 一致 (EXPECTED_CUTOFF=500, REL_CUTOFF=40)
echo 'CUTOFF=500' > /app/params.txt
sed -i 's/CUTOFF 200/CUTOFF 500/' /app/H2O.inp
/opt/cp2k/bin/cp2k.psmp -i /app/H2O.inp -o /app/H2O.out
grep "ENERGY| Total FORCE_EVAL" /app/H2O.out | awk '{print $NF}' > /app/out.txt
