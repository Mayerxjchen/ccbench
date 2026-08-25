#!/bin/bash
/opt/cp2k/bin/cp2k.psmp -i /app/H2O.inp -o /app/H2O.out
grep "ENERGY| Total FORCE_EVAL" /app/H2O.out | awk '{print $NF}' > /app/out.txt
