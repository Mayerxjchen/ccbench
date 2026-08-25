#!/bin/bash

#SBATCH -N 1
#SBATCH --job-name=deepmd
#SBATCH --partition=gpu,gpu-mig-2g-20gb
#SBATCH --gres=gpu:1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --time=24:00:00
#SBATCH --output=./slurm.out
#SBATCH --error=./slurm.out

set -e

source /etc/profile.d/modules.sh
module load anaconda/2022.5
eval "$(conda shell.bash hook)"
conda activate /public/groups/benchmark/libs/conda/deepmd/3.0.0b0-cuda118

export OMP_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=1
export TF_INTER_OP_PARALLELISM_THREADS=1
export TF_INTRA_OP_PARALLELISM_THREADS=4
export DP_INTER_OP_PARALLELISM_THREADS=1
export DP_INTRA_OP_PARALLELISM_THREADS=4
export LD_LIBRARY_PATH=/public/groups/benchmark/libs/conda/deepmd/3.0.0b0-cuda118/lib:$LD_LIBRARY_PATH
export XLA_FLAGS=--xla_gpu_cuda_data_dir=/public/groups/benchmark/libs/conda/deepmd/3.0.0b0-cuda118/lib

echo "CONDA_PREFIX=$CONDA_PREFIX"
echo "DP=$(command -v dp)"
dp --version
nvidia-smi || true
