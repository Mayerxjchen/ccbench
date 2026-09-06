#!/bin/bash
#SBATCH -N 1
#SBATCH --job-name=deepmd
#SBATCH --partition=cpu
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --time=24:00:00
#SBATCH --output=./slurm.out
#SBATCH --error=./slurm.out

set -e
source @ENV_SH@

# deepmd/tf single-process CPU parallelism (the v1 container image is CPU-only)
export OMP_NUM_THREADS=8
export OPENBLAS_NUM_THREADS=1
export TF_INTER_OP_PARALLELISM_THREADS=1
export TF_INTRA_OP_PARALLELISM_THREADS=8
export DP_INTER_OP_PARALLELISM_THREADS=1
export DP_INTRA_OP_PARALLELISM_THREADS=8

echo "DP=$(command -v dp)"
dp --version
