#!/usr/bin/env python3
"""生成 019-cp2k-eps-scf 的数值 reference。

用**最终评测镜像** `dftworld-base-cp2k` 里的真实 CP2K,固定 CUTOFF=400 + REL_CUTOFF=40,
扫一系列 EPS_SCF,解析每个的能量,以最紧 EPS_SCF 的能量为 reference,
程序化确定满足 target 的**最粗(最大数值)EPS_SCF**。

019 找"最粗仍达标",与 017 找"最小 CUTOFF"方向相反,但逻辑同一套。

用法:
    uv run python 019-cp2k-eps-scf/reference/generate_reference.py

输出:
    reference/reference.json
    reference/runs/<eps>.inp / .out
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # 019-cp2k-eps-scf/
TEMPLATE = ROOT / "environment" / "H2O.inp"
RUNS = ROOT / "reference" / "runs"
OUT_JSON = ROOT / "reference" / "reference.json"

IMAGE = "dftworld-base-cp2k"
CP2K = "/opt/cp2k/bin/cp2k.psmp"
WORK = "/work"

FIXED_CUTOFF = 400  # 任务固定 CUTOFF(instruction 声明,不改)
REL_CUTOFF = 40     # 显式固定,锁死数值环境
EPS_VALUES = [1.0e-4, 1.0e-5, 1.0e-6, 1.0e-7]  # instruction 允许的候选
TARGET = 1.0e-5     # 任务要求的 |E - E_ref| 上限


def build_input(eps: float) -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "REL_CUTOFF" not in text, "模板已有 REL_CUTOFF,请检查。"
    # 确保 CUTOFF 固定为任务约定值
    text = re.sub(r"CUTOFF\s+\d+", f"CUTOFF {FIXED_CUTOFF}", text)
    text = text.replace("EPS_SCF 1.0E-4", f"EPS_SCF {eps:.1E}")
    # 显式加 REL_CUTOFF(插到 CUTOFF 后)
    text = text.replace(f"CUTOFF {FIXED_CUTOFF}", f"CUTOFF {FIXED_CUTOFF}\n      REL_CUTOFF {REL_CUTOFF}")
    return text


def run_cp2k(eps: float) -> float:
    RUNS.mkdir(parents=True, exist_ok=True)
    tag = f"eps{eps:.0e}"
    inp = RUNS / f"{tag}.inp"
    out = RUNS / f"{tag}.out"
    inp.write_text(build_input(eps), encoding="utf-8")

    proc = subprocess.run(
        [
            "docker", "run", "--rm",
            "-v", f"{RUNS}:{WORK}",
            "-w", WORK,
            IMAGE,
            "bash", "-lc",
            f"{CP2K} -i {WORK}/{tag}.inp -o {WORK}/{tag}.out >/dev/null 2>&1",
        ],
        capture_output=True, text=True, timeout=900,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"EPS_SCF={eps} CP2K 失败: {proc.stderr[-300:]}")

    text = out.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"ENERGY\|\s+Total FORCE_EVAL.*\s(-?\d+\.\d+)", text)
    if not match:
        match = re.search(r"Total energy:\s+(-?\d+\.\d+)", text)
    if not match:
        raise RuntimeError(f"EPS_SCF={eps} 没解析到能量")
    return float(match.group(1))


def main() -> None:
    if not TEMPLATE.is_file():
        raise SystemExit(f"模板缺失:{TEMPLATE}")

    energies: dict[str, float] = {}
    for eps in EPS_VALUES:
        e = run_cp2k(eps)
        energies[f"{eps:.0e}"] = e
        print(f"EPS_SCF={eps:.0e}  E={e:.14f}")

    # reference = 最紧 EPS_SCF(最收敛)
    ref_eps = f"{min(EPS_VALUES):.0e}"
    e_ref = energies[ref_eps]

    rows = []
    for tag in sorted(energies, key=lambda t: float(t.replace('e', 'E'))):
        err = abs(energies[tag] - e_ref)
        rows.append({"eps_scf": tag, "energy": energies[tag], "error": err, "meets": err <= TARGET})

    # 程序化找"最粗(最大数值)仍满足 target"的 EPS_SCF
    meets = [r for r in rows if r["meets"]]
    expected = max(meets, key=lambda r: float(r["eps_scf"].replace('e', 'E'))) if meets else None

    ref = {
        "cp2k_version": "2025.2",
        "image": IMAGE,
        "cp2k_binary": CP2K,
        "fixed_cutoff": FIXED_CUTOFF,
        "rel_cutoff": REL_CUTOFF,
        "reference_eps_scf": ref_eps,
        "reference_energy": e_ref,
        "target": TARGET,
        "expected_eps_scf": expected["eps_scf"] if expected else None,
        "expected_energy": expected["energy"] if expected else None,
        "energies": {r["eps_scf"]: r["energy"] for r in rows},
        "errors": {r["eps_scf"]: r["error"] for r in rows},
        "meets": {r["eps_scf"]: r["meets"] for r in rows},
        "generated_by": str(ROOT / "reference" / "generate_reference.py"),
    }
    OUT_JSON.write_text(json.dumps(ref, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nreference.json → {OUT_JSON}")
    print(f"reference_eps_scf = {ref_eps}(最紧)")
    print(f"expected_eps_scf  = {ref['expected_eps_scf']}(最粗满足 |E-E_ref|<={TARGET:.0e})")
    if ref["expected_eps_scf"] is None:
        print("⚠ 没有任何 EPS_SCF 满足 target,需检查。")


if __name__ == "__main__":
    main()
