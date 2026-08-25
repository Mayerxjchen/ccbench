#!/bin/bash
set -e
source /app/.venv/bin/activate

# Fill template
sed -e 's/FILL_ME_TYPE_MAP/\["H", "C"\]/' \
    -e 's/FILL_ME_RCUT/6.00/' \
    -e 's/FILL_ME_NEURON/[25, 50, 100]/' \
    -e 's/FILL_ME_STEPS/2000/' \
    /app/input_template.json > /app/input.json

# Train
dp train /app/input.json

# Freeze
dp freeze -o /app/graph.pb

# Test and extract energy RMSE
dp test -m /app/graph.pb -s /app/data/validation_data 2>&1 | tee /app/test_output.log
grep -oP "energy RMSE\s*:\s*\K[\d.eE+-]+" /app/test_output.log > /app/out.txt
