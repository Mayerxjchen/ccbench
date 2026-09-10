#!/usr/bin/env bash
set -euo pipefail
export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/matplotlib}"
command -v claude >/dev/null
test "$(id -u)" = "10001"
test "${DISABLE_AUTOUPDATER:-}" = "1"
test "${CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC:-}" = "1"
claude --version

# Exercise the actual scientific utility surface, rather than merely checking
# that a package can be imported.  The lock contains ASE's runtime closure
# (including scipy/matplotlib) and Packmol must provide a working executable.
"${BENCH_PYTHON:-python}" - <<'PY'
from pathlib import Path
import tempfile

import ase
import importlib.util
import numpy
import scipy
import matplotlib
from ase import Atoms
from ase.io import write

assert numpy.__version__ == "1.26.4"
assert ase.__version__ == "3.26.0"
assert scipy.__version__ == "1.17.1"
assert matplotlib.__version__ == "3.11.1"
assert importlib.util.find_spec("jax") is None
assert importlib.util.find_spec("deepmd") is None
root = Path(tempfile.mkdtemp(prefix="bench-ase-smoke-"))
xyz = root / "water.xyz"
write(xyz, Atoms("H2O", positions=[(0, 0, 0), (0.76, 0, 0), (-0.24, 0.70, 0)]))
assert xyz.is_file() and xyz.stat().st_size > 0
PY
smoke_dir="$(mktemp -d /tmp/bench-candidate-smoke.XXXXXX)"
trap 'rm -rf "$smoke_dir"' EXIT
"${BENCH_PYTHON:-python}" - "$smoke_dir" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
(root / "water.xyz").write_text("3\nwater\nO 0 0 0\nH 0.76 0 0\nH -0.24 0.70 0\n", encoding="utf-8")
(root / "packmol.inp").write_text(
    f"tolerance 2.0\nfiletype xyz\noutput {root / 'packed.xyz'}\n"
    f"structure {root / 'water.xyz'}\n  number 1\n"
    "  inside box 0. 0. 0. 10. 10. 10.\nend structure\n",
    encoding="utf-8",
)
PY
packmol <"$smoke_dir/packmol.inp" >"$smoke_dir/packmol.log"
test -s "$smoke_dir/packed.xyz"
test "$(awk 'NR == 1 { print $1; exit }' "$smoke_dir/packed.xyz")" = "3"

for forbidden in cp2k lammps deepmd jax mpirun mpiexec srun sbatch ssh docker nvcc nvidia-smi singularity apptainer; do
  if command -v "$forbidden" >/dev/null 2>&1; then
    echo "forbidden executable present: $forbidden" >&2
    exit 1
  fi
done
echo CANDIDATE_BASE_SMOKE_OK
