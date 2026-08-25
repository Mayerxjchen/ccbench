#!/bin/bash
# Oracle solution —— 与 reference/reference.json 一致 (EXPECTED_EPS=1.0E-4, CUTOFF=400, REL_CUTOFF=40)
echo 'EPS_SCF=1.0E-4' > /app/params.txt
sed -i 's/EPS_SCF 1.0E-4/EPS_SCF 1.0E-4/' /app/H2O.inp
/opt/cp2k/bin/cp2k.psmp -i /app/H2O.inp -o /app/H2O.out
grep "ENERGY| Total FORCE_EVAL" /app/H2O.out | awk '{print $NF}' > /app/out.txt
