#!/bin/bash
# 042 expert container environment (deepmd-jax runtime).
# Source this from every stage script so the venv + jax env are consistent.
set -u

export OMP_NUM_THREADS=${OMP_NUM_THREADS:-4}
export JAX_ENABLE_X64=1
export XLA_FLAGS="--xla_gpu_cuda_data_dir=${CUDA_HOME:-/usr/local/cuda}"
export PYTHONUNBUFFERED=1

# deepmd-jax venv (image-provided); fall back to system python.
if [ -x /opt/deepmd-jax/bin/python ]; then
  export PYTHON=/opt/deepmd-jax/bin/python
  export PATH=/opt/deepmd-jax/bin:$PATH
elif [ -x /opt/ai2kit/bin/python ]; then
  export PYTHON=/opt/ai2kit/bin/python
  export PATH=/opt/ai2kit/bin:$PATH
else
  export PYTHON=python3
fi

export AI2KIT_042_PROFILE=${AI2KIT_042_PROFILE:-smoke}
export WAIT_JOB_INTERVAL=5   # pseudo-slurm test-bed cadence
