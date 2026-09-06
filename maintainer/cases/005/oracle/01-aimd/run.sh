#!/bin/bash
# 042 stage 1 — CP2K revPBE-D3 AIMD reference labeling.
# Generates a labeled DeepMD raw set from the structure built by stage 00.
#
# Path contract (all resolved from the case root, never the script dir):
#   structures/<iface>/{box,coord,type}.raw   <- stage 00 output
#   work/<iface>/coord_n_cell.inc             <- generated (tools/gen_coord_inc.py)
#   work/<iface>/run.inp + coord_n_cell.inc   <- CP2K job working dir
# The AIMD job runs with its CWD = work/<iface>, so @INCLUDE
# coord_n_cell.inc resolves inside the job exactly like on-site.
set -euo pipefail

CASE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$CASE_ROOT"
source "solution/expert/env.sh"
IFACE="${1:-graphene-water}"

if [ "$AI2KIT_042_PROFILE" = "formal" ]; then MD_STEPS=2000; else MD_STEPS=20; fi

STRUCT_DIR="structures/$IFACE"
if [ ! -f "$STRUCT_DIR/coord.raw" ]; then
  echo "[01-aimd] ERROR: structure not found: $STRUCT_DIR" >&2
  echo "  Run 00-structure-generation first." >&2
  exit 1
fi

JOB_DIR="work/$IFACE"
mkdir -p "$JOB_DIR"

# --- 1. DeepMD raw -> CP2K include (shared tool; elements from public map) --
"$PYTHON" solution/expert/tools/gen_coord_inc.py \
  --iface "$IFACE" --struct-dir "$STRUCT_DIR" --case-root . --out-root work

# --- 2. render input into the JOB dir and submit -----------------------------
sed "s/@STEPS@/$MD_STEPS/" solution/expert/01-aimd/aimd.inp > "$JOB_DIR/run.inp"

JOB=$(sbatch --parsable --job-name="aimd-$IFACE" --ntasks=4 --cpus-per-task=4 <<SL
#!/bin/bash
#SBATCH --job-name=aimd-$IFACE
#SBATCH --ntasks=4
#SBATCH --cpus-per-task=4
cd "$CASE_ROOT/$JOB_DIR"
export OMP_NUM_THREADS=4
export CP2K_DATA_DIR=${CP2K_DATA_DIR:-/opt/cp2k/share/cp2k/data}
cp2k.psmp -i run.inp -o aimd.out
SL
)
JOB="$(echo "$JOB" | awk '/^[0-9]+$/{print $1}')"
echo "[01-aimd] submitted $IFACE as job $JOB ($MD_STEPS MD steps)"

# --- 3. wait for scheduler-terminal state (never convert mid-run) ------------
bash "solution/expert/wait_job.sh" "$JOB" 3600

# --- 4. post-processing ------------------------------------------------------
"$PYTHON" solution/expert/01-aimd/convert.py "$IFACE"
echo "[01-aimd] done: work/$IFACE labeled set"
