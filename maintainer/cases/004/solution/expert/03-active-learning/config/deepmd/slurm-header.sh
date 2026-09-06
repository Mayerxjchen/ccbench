#!/bin/bash
#SBATCH -N 1
#SBATCH --job-name=deepmd
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=@DEEPMD_CPUS@
#SBATCH --time=24:00:00
#SBATCH --output=@SLURM_OUT@
#SBATCH --error=@SLURM_ERR@

set -e
source @ENV_SH@

# deepmd/tf single-process CPU parallelism (the v1 container image is CPU-only)
export OMP_NUM_THREADS=@DEEPMD_CPUS@
export OPENBLAS_NUM_THREADS=1
export TF_INTER_OP_PARALLELISM_THREADS=1
export TF_INTRA_OP_PARALLELISM_THREADS=@DEEPMD_CPUS@
export DP_INTER_OP_PARALLELISM_THREADS=1
export DP_INTRA_OP_PARALLELISM_THREADS=@DEEPMD_CPUS@

echo "DP=$(command -v dp)"
dp --version
