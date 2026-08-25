#!/usr/bin/env python3
"""生成 017-cp2k-cutoff 的数值 reference。

用**最终评测镜像** `dftworld-base-cp2k` 里的真实 CP2K,按固定输入跑一系列 CUTOFF,
解析每个的能量,以 `reference_cutoff` 的能量为基准,程序化确定满足 target 的最小 CUTOFF。

用法:
    uv run python 017-cp2k-cutoff/reference/generate_reference.py [--cutoffs 200,280,320,360,400,500,600,700]

输出:
    reference/reference.json   ← 单一真相,convergence_guide/tests/solution 都从这里派生
    reference/runs/<cutoff>.inp / .out   ← 原始运行留档

关键约定:
    - 镜像      dftworld-base-cp2k(与评测 Agent 完全一致)
    - binary    /opt/cp2k/bin/cp2k.psmp
    - geometry  cell / PBE / basis / potential / EPS_SCF 全固定,取自 H2O.inp 模板
    - REL_CUTOFF 显式固定为 40(锁死数值环境,防 CP2K 默认值漂移)
    - 能量解析  用 `ENERGY| Total FORCE_EVAL` 行(与 solution/agent 提取口径一致)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # 017-cp2k-cutoff/
TEMPLATE = ROOT / "environment" / "H2O.inp"
RUNS = ROOT / "reference" / "runs"
OUT_JSON = ROOT / "reference" / "reference.json"

IMAGE = "dftworld-base-cp2k"
CP2K = "/opt/cp2k/bin/cp2k.psmp"
WORK = "/work"  # 容器内挂载目录

DEFAULT_CUTOFFS = [200, 280, 320, 360, 400, 500, 600, 700]
REFERENCE_CUTOFF = 600  # E600 已实测收敛,作统一 reference
TARGET = 1.0e-5  # 任务要求的 |E - E_ref| 上限


def build_input(cutoff: int) -> str:
    """从模板派生:改 CUTOFF,显式固定 REL_CUTOFF 40。"""
    text = TEMPLATE.read_text(encoding="utf-8")
    if "REL_CUTOFF" in text:
        raise SystemExit(f"模板 {TEMPLATE} 已有 REL_CUTOFF,请先检查。")
    text = text.replace("CUTOFF 200", f"CUTOFF {cutoff}\n      REL_CUTOFF 40")
    return text


def run_cp2k(cutoff: int) -> float:
    """在容器里跑一次 CP2K,返回 ENERGY| Total FORCE_EVAL 能量。"""
    RUNS.mkdir(parents=True, exist_ok=True)
    inp = RUNS / f"cut{cutoff}.inp"
    out = RUNS / f"cut{cutoff}.out"
    inp.write_text(build_input(cutoff), encoding="utf-8")

    # 容器只读挂载 reference/runs,避免污染任务目录
    proc = subprocess.run(
        [
            "docker", "run", "--rm",
            "-v", f"{RUNS}:{WORK}",
            "-w", WORK,
            IMAGE,
            "bash", "-lc",
            f"{CP2K} -i {WORK}/cut{cutoff}.inp -o {WORK}/cut{cutoff}.out >/dev/null 2>&1",
        ],
        capture_output=True, text=True, timeout=900,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"CUTOFF={cutoff} CP2K 失败: {proc.stderr[-300:]}")

    text = out.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"ENERGY\|\s+Total FORCE_EVAL.*\s(-?\d+\.\d+)", text)
    if not match:
        # 兜底:找 Total energy
        match = re.search(r"Total energy:\s+(-?\d+\.\d+)", text)
    if not match:
        raise RuntimeError(f"CUTOFF={cutoff} 没解析到能量")
    return float(match.group(1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--cutoffs", type=str,
        default=",".join(str(c) for c in DEFAULT_CUTOFFS),
        help="要扫描的 CUTOFF 列表,逗号分隔",
    )
    args = ap.parse_args()
    cutoffs = [int(c) for c in args.cutoffs.split(",") if c.strip()]

    if not TEMPLATE.is_file():
        raise SystemExit(f"模板缺失:{TEMPLATE}")

    energies: dict[int, float] = {}
    for cutoff in cutoffs:
        e = run_cp2k(cutoff)
        energies[cutoff] = e
        print(f"CUTOFF={cutoff:>4}  E={e:.14f}  err=|E-E{REFERENCE_CUTOFF}|={abs(e - energies.get(REFERENCE_CUTOFF, e)):.3e}" if cutoff != REFERENCE_CUTOFF else f"CUTOFF={cutoff:>4}  E={e:.14f}  ← reference")

    # reference 与错误表
    e_ref = energies[REFERENCE_CUTOFF]
    rows = []
    for c in sorted(energies):
        err = abs(energies[c] - e_ref)
        rows.append({"cutoff": c, "energy": energies[c], "error": err, "meets": err <= TARGET})

    # 程序化找满足 target 的最小 CUTOFF
    meets = [r for r in rows if r["meets"]]
    expected_cutoff = min(r["cutoff"] for r in meets) if meets else None

    ref = {
        "cp2k_version": "2025.2",
        "image": IMAGE,
        "cp2k_binary": CP2K,
        "rel_cutoff": 40,
        "reference_cutoff": REFERENCE_CUTOFF,
        "reference_energy": e_ref,
        "target": TARGET,
        "expected_cutoff": expected_cutoff,
        "energies": {str(r["cutoff"]): r["energy"] for r in rows},
        "errors": {str(r["cutoff"]): r["error"] for r in rows},
        "meets": {str(r["cutoff"]): r["meets"] for r in rows},
        "generated_by": str(ROOT / "reference" / "generate_reference.py"),
    }
    OUT_JSON.write_text(json.dumps(ref, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nreference.json → {OUT_JSON}")
    print(f"expected_cutoff = {expected_cutoff}(最小满足 |E-E{REFERENCE_CUTOFF}|<={TARGET:.0e})")
    if expected_cutoff is None:
        print("⚠ 没有任何 CUTOFF 满足 target,需扩大扫描范围!")


if __name__ == "__main__":
    main()
