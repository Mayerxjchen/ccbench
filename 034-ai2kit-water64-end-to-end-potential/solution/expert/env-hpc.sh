#!/bin/bash
# Native-HPC environment for case 034. It only activates an existing private
# environment; it never creates or changes the cluster Anaconda base.

_HPC_ENV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export EXPERT_DIR="$_HPC_ENV_DIR"
export CASE_ROOT="$(cd "$EXPERT_DIR/../.." && pwd)"

# Pinned compatibility contract, recorded verbatim in provenance.
export AI2KIT_REQUIRED_DEEPMD="deepmd-kit==2.2.11"
export AI2KIT_REQUIRED_AI2KIT="ai2-kit==1.1.0"
export AI2KIT_REQUIRED_OMB="oh-my-batch==0.7.6"

: "${AI2KIT_CONDA_ENV:?set AI2KIT_CONDA_ENV to a user-owned absolute prefix}"
: "${AI2KIT_034_WORKSPACE:?set AI2KIT_034_WORKSPACE to the remote shared workspace}"
case "$AI2KIT_CONDA_ENV" in
  /*) ;;
  *) echo "env-hpc: AI2KIT_CONDA_ENV must be absolute" >&2; return 1 ;;
esac
if [ ! -x "$AI2KIT_CONDA_ENV/bin/python" ]; then
  echo "env-hpc: private environment has no executable bin/python: $AI2KIT_CONDA_ENV" >&2
  return 1
fi

if [ -r "${AI2KIT_MODULE_INIT:-/etc/profile.d/modules.sh}" ]; then
  # shellcheck disable=SC1090
  source "${AI2KIT_MODULE_INIT:-/etc/profile.d/modules.sh}"
fi
# Scientific engine modules are deliberately NOT loaded here.  CP2K 2024.3
# requires oneAPI 2023.2 while the pinned LAMMPS requires oneAPI 2021.1; mixing
# both runtimes caused deterministic Fortran-154 failures in real DFT jobs.
# hpc-runtime.sh loads exactly one engine stack at each process boundary.
export AI2KIT_CP2K_MODULES="${AI2KIT_CP2K_MODULES:-}"
export AI2KIT_LAMMPS_MODULES="${AI2KIT_LAMMPS_MODULES:-}"

export AI2KIT_VENV="$AI2KIT_CONDA_ENV"
export CONDA_PREFIX="$AI2KIT_CONDA_ENV"
export PATH="$AI2KIT_CONDA_ENV/bin:$PATH"
export AI2KIT_CP2K_NP="${AI2KIT_CP2K_NP:-4}"
export AI2KIT_CP2K_OMP="${AI2KIT_CP2K_OMP:-2}"
export AI2KIT_CP2K_BIN="${AI2KIT_CP2K_BIN:?set AI2KIT_CP2K_BIN from probe evidence}"
export AI2KIT_CP2K_LAUNCHER="${AI2KIT_CP2K_LAUNCHER:?set AI2KIT_CP2K_LAUNCHER to srun or mpirun from probe evidence}"
export AI2KIT_LAMMPS_MODE="${AI2KIT_LAMMPS_MODE:?set AI2KIT_LAMMPS_MODE to builtin, plugin, or matched-lmp}"
export AI2KIT_LAMMPS_BIN="${AI2KIT_LAMMPS_BIN:-lmp}"

if [ -n "${AI2KIT_SLURM_PARTITION:-}" ]; then export SBATCH_PARTITION="$AI2KIT_SLURM_PARTITION"; fi
if [ -n "${AI2KIT_SLURM_ACCOUNT:-}" ]; then export SBATCH_ACCOUNT="$AI2KIT_SLURM_ACCOUNT"; fi
if [ -n "${AI2KIT_SLURM_QOS:-}" ]; then export SBATCH_QOS="$AI2KIT_SLURM_QOS"; fi

# Reuse the scientific profile and path definitions from the container layer.
# All relevant variables above are already fixed, so env.sh preserves them.
# shellcheck disable=SC1091
source "$EXPERT_DIR/env.sh"

export OMP_NUM_THREADS="$AI2KIT_CP2K_OMP"
export WAIT_JOB_INTERVAL="${WAIT_JOB_INTERVAL:-60}"
export AI2KIT_SLURM_LOG_DIR="${AI2KIT_SLURM_LOG_DIR:-$AI2KIT_WORK_DIR/slurm-logs}"
mkdir -p "$AI2KIT_SLURM_LOG_DIR"
export SBATCH_OUTPUT="$AI2KIT_SLURM_LOG_DIR/slurm-%j.out"
export SBATCH_ERROR="$AI2KIT_SLURM_LOG_DIR/slurm-%j.out"

echo "[env-hpc.sh] private_env=$AI2KIT_CONDA_ENV"
echo "[env-hpc.sh] cp2k_modules=${AI2KIT_CP2K_MODULES:-<none>}"
echo "[env-hpc.sh] lammps_modules=${AI2KIT_LAMMPS_MODULES:-<none>}"
echo "[env-hpc.sh] cp2k=$AI2KIT_CP2K_BIN launcher=$AI2KIT_CP2K_LAUNCHER np=$AI2KIT_CP2K_NP omp=$AI2KIT_CP2K_OMP"
echo "[env-hpc.sh] lammps=$AI2KIT_LAMMPS_BIN mode=$AI2KIT_LAMMPS_MODE"
