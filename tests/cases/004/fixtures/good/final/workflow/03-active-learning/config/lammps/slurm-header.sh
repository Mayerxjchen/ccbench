#!/bin/bash
#SBATCH -N 1
#SBATCH --job-name=lammps
#SBATCH --partition=cpu
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=12:00:00
#SBATCH --output=./slurm.out
#SBATCH --error=./slurm.out

set -e
source @ENV_SH@

export OMP_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=1
export TF_INTER_OP_PARALLELISM_THREADS=1
export TF_INTRA_OP_PARALLELISM_THREADS=4
export DP_INTER_OP_PARALLELISM_THREADS=1
export DP_INTRA_OP_PARALLELISM_THREADS=4

echo "LMP=$(command -v lmp)"
lmp -h >/dev/null
