#!/bin/bash
#SBATCH -N 1
#SBATCH --job-name=cp2k
#SBATCH --ntasks=@CP2K_NP@
#SBATCH --cpus-per-task=@CP2K_OMP@
#SBATCH --time=8:00:00
#SBATCH --output=@SLURM_OUT@
#SBATCH --error=@SLURM_ERR@

set -e
source @ENV_SH@
export OMP_NUM_THREADS=@CP2K_OMP@
