"""Local AIMD input preflight — exercises the REAL runner building blocks.

Layout produced here mirrors run.sh exactly:

    <tmp>/public/system.json                 (copied from the case)
    <tmp>/structures/preflight/{box,coord,type}.raw   (synthetic mini system)
    <tmp>/work/preflight/coord_n_cell.inc    (REAL tools/gen_coord_inc.py)
    <tmp>/work/preflight/run.inp             (REAL aimd.inp, @STEPS@->1)

CP2K then runs with CWD = work/preflight inside the ai2kit image — identical
include resolution to the submitted job. PASS requires reaching SCF (input
fully accepted); convergence is NOT required from random coordinates.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

CASE = Path(__file__).resolve().parents[1]
sysmap = json.loads((CASE / "public/system.json").read_text())["element_type_map"]

work = Path(tempfile.mkdtemp(prefix="aimd-preflight-"))
(work / "public").mkdir(parents=True)
(work / "structures/preflight").mkdir(parents=True)
(work / "work/preflight").mkdir(parents=True)

# synthetic mini interface covering O/H/C kinds
types = [sysmap["C"], sysmap["O"], sysmap["O"],
         sysmap["H"], sysmap["H"], sysmap["H"], sysmap["H"], sysmap["O"]]
coords = [
    [0.0, 0.0, 12.0], [1.42, 0.0, 12.0],
    [3.0, 3.0, 20.0], [4.0, 3.0, 20.0], [3.5, 3.9, 20.0],
    [8.0, 8.0, 22.0], [9.0, 8.0, 22.0], [8.5, 8.9, 22.0],
]
sd = work / "structures/preflight"
(sd / "type.raw").write_text("\n".join(map(str, types)) + "\n")
(sd / "coord.raw").write_text(
    "\n".join(" ".join(f"{c:.6f}" for c in xyz) for xyz in coords) + "\n")
(sd / "box.raw").write_text(
    "\n".join(" ".join(f"{v:.6f}" for v in row)
              for row in [[14.76, 0, 0], [0, 12.783, 0], [0, 0, 30.0]]))
shutil_copy_src = CASE / "public/system.json"
import shutil
shutil.copy(shutil_copy_src, work / "public/system.json")

# 1. REAL shared generator, invoked like run.sh does
gen = CASE / "solution/expert/tools/gen_coord_inc.py"
r = subprocess.run([sys.executable, str(gen), "--iface", "preflight",
                    "--struct-dir", str(sd), "--case-root", str(work),
                    "--out-root", "work"], cwd=str(work),
                   capture_output=True, text=True)
print("[gen_coord_inc]", r.stdout.strip() or r.stderr.strip())
assert r.returncode == 0

# 2. REAL template rendered into the job dir (same sed as run.sh)
inp = (CASE / "solution/expert/01-aimd/aimd.inp").read_text().replace("@STEPS@", "1")
run_inp = work / "work/preflight/run.inp"
run_inp.write_text(inp)

# 3. real container execution, CWD = job dir
proc = subprocess.run(
    ["docker", "run", "--rm", "-v", f"{work}:/case", "-w", "/case/work/preflight",
     "--entrypoint", "bash", "dftworld-base-ai2kit:0.1.0-cpu", "-c",
     "export OMP_NUM_THREADS=2 JAX_ENABLE_X64=1 "
     "CP2K_DATA_DIR=/opt/cp2k/share/cp2k/data; "
     "timeout 150 cp2k.psmp -i run.inp > out.log 2>&1; echo rc=$?; "
     "grep -qE 'OPT|SCF' out.log && echo PARSE_REACHED_SCF || "
     "{ echo PARSE_FAIL; tail -14 out.log; }"],
    capture_output=True, text=True, timeout=400,
)
print("\n".join(proc.stdout.strip().splitlines()[-6:]))
ok = "PARSE_REACHED_SCF" in proc.stdout
if not ok and proc.stderr.strip():
    print("[docker stderr]", proc.stderr.strip()[-400:])
print("PREFLIGHT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
