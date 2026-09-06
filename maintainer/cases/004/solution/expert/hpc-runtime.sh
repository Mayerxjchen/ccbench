#!/bin/bash
# Shared runtime helpers for container and native-HPC execution.

_AI2KIT_RUNTIME_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ai2kit_source_env() {
  local selected="${AI2KIT_ENV_FILE:-$_AI2KIT_RUNTIME_DIR/env.sh}"
  if [ ! -f "$selected" ]; then
    echo "runtime: environment file not found: $selected" >&2
    return 1
  fi
  # shellcheck disable=SC1090
  source "$selected"
}

ai2kit_load_engine_modules() {
  local modules="$1"
  [ -n "$modules" ] || return 0
  if ! type module >/dev/null 2>&1; then
    local init="${AI2KIT_MODULE_INIT:-/etc/profile.d/modules.sh}"
    [ -r "$init" ] || { echo "runtime: module init not readable: $init" >&2; return 1; }
    # shellcheck disable=SC1090
    source "$init"
  fi
  module purge || return 1
  # Intentional word splitting: Environment Modules accepts a module list.
  # shellcheck disable=SC2086
  module load $modules
}

ai2kit_run_cp2k() {
  local np="$1"
  local input="$2"
  local output="$3"
  local launcher="${AI2KIT_CP2K_LAUNCHER:-mpirun}"
  local binary="${AI2KIT_CP2K_BIN:-cp2k.psmp}"

  ai2kit_load_engine_modules "${AI2KIT_CP2K_MODULES:-}" || return 1

  if ! command -v "$binary" >/dev/null 2>&1; then
    echo "runtime: CP2K binary not found: $binary" >&2
    return 1
  fi
  export OMP_NUM_THREADS="${AI2KIT_CP2K_OMP:-1}"
  case "$launcher" in
    srun)
      if [ -n "${AI2KIT_CP2K_PMI_LIBRARY:-}" ]; then
        [ -f "$AI2KIT_CP2K_PMI_LIBRARY" ] || {
          echo "runtime: CP2K PMI library not found: $AI2KIT_CP2K_PMI_LIBRARY" >&2
          return 1
        }
        export I_MPI_PMI_LIBRARY="$AI2KIT_CP2K_PMI_LIBRARY"
      fi
      srun --ntasks="$np" --cpus-per-task="$OMP_NUM_THREADS" "$binary" -i "$input" > "$output" 2>&1
      ;;
    mpirun)
      mpirun -np "$np" "$binary" -i "$input" > "$output" 2>&1
      ;;
    direct)
      if [ "$np" -ne 1 ]; then
        echo "runtime: direct CP2K launcher requires np=1" >&2
        return 1
      fi
      "$binary" -i "$input" > "$output" 2>&1
      ;;
    *)
      echo "runtime: unsupported AI2KIT_CP2K_LAUNCHER=$launcher" >&2
      return 1
      ;;
  esac
}

ai2kit_lammps_plugin_command() {
  case "${AI2KIT_LAMMPS_MODE:-plugin}" in
    plugin) if [ ! -f "${DEEPMD_PLUGIN:-}" ]; then
        echo "runtime: plugin mode requires a real DEEPMD_PLUGIN file" >&2
        return 1
      fi
      printf 'plugin load %s\n' "$DEEPMD_PLUGIN"
      ;;
    builtin)
      printf '# DeePMD is built into the selected LAMMPS\n'
      ;;
    matched-lmp)
      printf '# Matched DeePMD LAMMPS executable loads its packaged runtime\n'
      ;;
    *)
      echo "runtime: AI2KIT_LAMMPS_MODE must be builtin, plugin, or matched-lmp" >&2
      return 1
      ;;
  esac
}

ai2kit_validate_lammps() {
  local binary="${AI2KIT_LAMMPS_BIN:-lmp}"
  if ! command -v "$binary" >/dev/null 2>&1; then
    echo "runtime: LAMMPS binary not found: $binary" >&2
    return 1
  fi
  ai2kit_lammps_plugin_command >/dev/null
}

ai2kit_lammps_prepend_ld_library_path() {
  # Native-HPC plugin ABI (job 3537330): libdeepmd_lmpplugin.so dlopens
  # libtensorflow_cc.so.2 from the TF wheel plus its own deepmd lib dir, and the
  # pinned cluster LAMMPS has NO lmp-wrapper (the container's /opt/lmp-wrapper
  # does this per-invocation).  Prepend both dirs, scoped to this process — a
  # per-job export, so cp2k sub-jobs (separate processes) never see them (env.sh
  # note: cp2k must not load OUR libdeepmd_cc.so).  Idempotent; no-op when the
  # venv is unset or the libs are absent.
  local venv="${AI2KIT_VENV:-}" tf_lib tf_dir dp_lib dp_dir d
  [ -n "$venv" ] || return 0
  tf_lib="$(find "$venv" -maxdepth 6 -type f -name 'libtensorflow_cc.so.2' 2>/dev/null | head -n 1 || true)"
  dp_lib="$(find "$venv" -maxdepth 6 -type f -name 'libdeepmd_lmpplugin.so' 2>/dev/null | head -n 1 || true)"
  tf_dir=""; dp_dir=""
  [ -n "$tf_lib" ] && tf_dir="$(dirname "$tf_lib")"
  [ -n "$dp_lib" ] && dp_dir="$(dirname "$dp_lib")"
  for d in "$tf_dir" "$dp_dir"; do
    [ -n "$d" ] && [ -d "$d" ] || continue
    case ":${LD_LIBRARY_PATH:-}:" in
      *":$d:"*) ;;
      *) LD_LIBRARY_PATH="$d${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" ;;
    esac
  done
  export LD_LIBRARY_PATH
}

ai2kit_lmp() {
  # LAMMPS launcher: prepend the plugin ABI loader path, then run the binary.
  # Uses AI2KIT_LAMMPS_BIN (G7.6-pinned absolute path on HPC); bare `lmp` is a
  # container-only convenience and does NOT resolve on the cluster (module
  # build dir has no `lmp` symlink; the venv does not install deepmd-kit[lmp]).
  ai2kit_load_engine_modules "${AI2KIT_LAMMPS_MODULES:-}" || return 1
  ai2kit_lammps_prepend_ld_library_path
  exec "${AI2KIT_LAMMPS_BIN:-lmp}" "$@"
}
