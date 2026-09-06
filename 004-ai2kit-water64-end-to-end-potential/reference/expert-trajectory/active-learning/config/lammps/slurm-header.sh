#!/bin/bash

#SBATCH -N 1
#SBATCH --job-name=lammps
#SBATCH --partition=gpu,gpu-mig-2g-20gb
#SBATCH --gres=gpu:1
#SBATCH --time=12:00:00
#SBATCH --output=./slurm.out
#SBATCH --error=./slurm.out

set -e

source /etc/profile.d/modules.sh
module load anaconda/2022.5
module load gcc/12.1
module load gsl/2.8
module load mpi/openmpi/4.0.3-gcc
eval "$(conda shell.bash hook)"
conda activate /public/groups/benchmark/libs/conda/deepmd/3.0.0b0-cuda118

export OMP_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=1
export TF_INTER_OP_PARALLELISM_THREADS=1
export TF_INTRA_OP_PARALLELISM_THREADS=4
export DP_INTER_OP_PARALLELISM_THREADS=1
export DP_INTRA_OP_PARALLELISM_THREADS=4
export LD_LIBRARY_PATH=/public/groups/benchmark/libs/conda/deepmd/3.0.0b0-cuda118/lib:$LD_LIBRARY_PATH
export LAMMPS_PLUGIN_PATH=/public/groups/benchmark/libs/conda/deepmd/3.0.0b0-cuda118/opt/deepmd/lib

echo "CONDA_PREFIX=$CONDA_PREFIX"
echo "LMP=$(command -v lmp)"
lmp -h >/dev/null
nvidia-smi || true
