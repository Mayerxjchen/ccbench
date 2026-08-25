#!/bin/bash
set -e
source /app/.venv/bin/activate

# Oracle solution —— 与 reference/reference.json 一致 (EXPECTED_RCUT=4.0)
echo 'RCUT=4.0' > /app/params.txt
sed 's/FILL_ME_RCUT/4.0/' /app/input_template.json > /app/input.json

dp train /app/input.json
dp freeze -o /app/graph.pb
dp test -m /app/graph.pb -s /app/data/validation_data 2>&1 | tee /app/test_output.log
# 提取 energy RMSE (eV), 兼容含/不含空格的输出
grep -oE "Energy RMSE[[:space:]]*:[[:space:]]*[0-9.eE+-]+" /app/test_output.log \
  | grep -oE "[0-9.eE+-]+$" \
  | tail -1 > /app/out.txt
