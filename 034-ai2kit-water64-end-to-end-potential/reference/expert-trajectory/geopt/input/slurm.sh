#!/bin/bash

#SBATCH -N 1
#SBATCH --job-name=water-geopt
#SBATCH --partition=cpu
#SBATCH --ntasks=64
#SBATCH --time=12:00:00
#SBATCH --output=/public/home/<site-user>/ai2kit/ai2kit/data/cp2k-geopt/output/slurm.out
#SBATCH --error=/public/home/<site-user>/ai2kit/ai2kit/data/cp2k-geopt/output/slurm.out

set -e

source /etc/profile.d/modules.sh
module load cp2k/2024.3
export OMP_NUM_THREADS=1

mkdir -p /public/home/<site-user>/ai2kit/ai2kit/data/cp2k-geopt/output
cd /public/home/<site-user>/ai2kit/ai2kit/data/cp2k-geopt/output
ln -sf /public/home/<site-user>/ai2kit/ai2kit/data/cp2k-geopt/input/coord_n_cell.inc .
mpirun cp2k.psmp -i /public/home/<site-user>/ai2kit/ai2kit/data/cp2k-geopt/input/geopt.inp > output 2>&1
