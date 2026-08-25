#!/bin/bash
set -e
source /app/.venv/bin/activate

# Train
dp train /app/input.json

# Freeze
dp freeze -o /app/graph.pb

# Test and extract energy RMSE
dp test -m /app/graph.pb -s /app/data/validation_data 2>&1 | tee /app/test_output.log
grep -oP "energy RMSE\s*:\s*\K[\d.eE+-]+" /app/test_output.log > /app/out.txt
