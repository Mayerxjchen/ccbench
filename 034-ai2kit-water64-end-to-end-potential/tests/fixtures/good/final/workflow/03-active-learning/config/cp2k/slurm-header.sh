#!/bin/bash
#SBATCH -N 1
#SBATCH --job-name=cp2k
#SBATCH --partition=cpu
#SBATCH --ntasks=4
#SBATCH --cpus-per-task=1
#SBATCH --time=8:00:00
#SBATCH --output=./slurm.out
#SBATCH --error=./slurm.out

set -e
source @ENV_SH@
export OMP_NUM_THREADS=1
