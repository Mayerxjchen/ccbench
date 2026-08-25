#!/bin/bash
#SBATCH -N 1
#SBATCH --job-name=lammps
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=@LAMMPS_CPUS@
#SBATCH --time=12:00:00
#SBATCH --output=@SLURM_OUT@
#SBATCH --error=@SLURM_ERR@

set -e
source @ENV_SH@
# Engine-scoped module isolation: load ONLY the LAMMPS stack (oneAPI 2021.1),
# never the CP2K stack (oneAPI 2023.2) that collides with it.  Route through the
# runtime helper so this header can never drift from ai2kit_lmp's own load.
source "${EXPERT_DIR:?}/hpc-runtime.sh"
ai2kit_load_engine_modules "${AI2KIT_LAMMPS_MODULES:-}" || exit 1

export OMP_NUM_THREADS=@LAMMPS_CPUS@
export OPENBLAS_NUM_THREADS=1
export TF_INTER_OP_PARALLELISM_THREADS=1
export TF_INTRA_OP_PARALLELISM_THREADS=@LAMMPS_CPUS@
export DP_INTER_OP_PARALLELISM_THREADS=1
export DP_INTRA_OP_PARALLELISM_THREADS=@LAMMPS_CPUS@

# Bare `lmp` does NOT resolve on the cluster (module build dir has no symlink;
# the venv installs deepmd-kit without the [lmp] extra).  Use the pinned
# AI2KIT_LAMMPS_BIN from env-hpc.sh; fall back to `lmp` (container wrapper).
echo "LMP=${AI2KIT_LAMMPS_BIN:-lmp}"
"${AI2KIT_LAMMPS_BIN:-lmp}" -h >/dev/null
