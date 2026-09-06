#!/bin/bash

#SBATCH -N 1
#SBATCH --job-name=cp2k
#SBATCH --partition=cpu
#SBATCH --ntasks=64
#SBATCH --time=8:00:00
#SBATCH --output=./slurm.out
#SBATCH --error=./slurm.out

set -e

source /etc/profile.d/modules.sh
module load cp2k/2024.3
export OMP_NUM_THREADS=1
