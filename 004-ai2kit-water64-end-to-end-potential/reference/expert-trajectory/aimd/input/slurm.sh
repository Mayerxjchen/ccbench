#!/bin/bash

#SBATCH -N 1
#SBATCH --job-name=water-aimd
#SBATCH --partition=cpu
#SBATCH --ntasks=64
#SBATCH --time=24:00:00
#SBATCH --output=/public/home/<site-user>/ai2kit/ai2kit/data/cp2k-aimd/output/slurm.out
#SBATCH --error=/public/home/<site-user>/ai2kit/ai2kit/data/cp2k-aimd/output/slurm.err

set -e

source /etc/profile.d/modules.sh
module load cp2k/2024.3
export OMP_NUM_THREADS=1

# 创建输出目录
mkdir -p /public/home/<site-user>/ai2kit/ai2kit/data/cp2k-aimd/output

# 复制输入文件到输出目录
cp /public/home/<site-user>/ai2kit/ai2kit/data/cp2k-aimd/input/cp2k_aimd.inp /public/home/<site-user>/ai2kit/ai2kit/data/cp2k-aimd/output/
cp /public/home/<site-user>/ai2kit/ai2kit/data/cp2k-aimd/input/coord_n_cell.inc /public/home/<site-user>/ai2kit/ai2kit/data/cp2k-aimd/output/

cd /public/home/<site-user>/ai2kit/ai2kit/data/cp2k-aimd/output
mpirun cp2k.psmp -i cp2k_aimd.inp > water_aimd_output 2>&1
