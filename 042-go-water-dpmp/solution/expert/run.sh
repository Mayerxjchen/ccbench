#!/bin/bash
# 042 expert workflow orchestrator:
# 00-structure-generation -> 01-aimd -> 02-train -> 03-active-learning ->
# 04-validation -> manifest_writer.py -> final/manifest.json
set -euo pipefail
cd "$(dirname "$0")"
source ./env.sh

echo "[run] profile=${AI2KIT_042_PROFILE}"

./00-structure-generation/build_all_interfaces.py
./01-aimd/run.sh
./02-train/run.sh
./03-active-learning/run.sh
./04-validation/run.sh

"$PYTHON" manifest_writer.py
echo "[run] done -> final/manifest.json"
