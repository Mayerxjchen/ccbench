"""017-cp2k-cutoff 测试。

数值来源:``reference/reference.json``(由 ``generate_reference.py`` 用最终评测镜像
``dftworld-base-cp2k`` / CP2K 2025.2 / REL_CUTOFF=40 实跑生成)。

关键设计:不再用 E_ref 直接检查 Agent 输出。因为任务要求 Agent 用**选定的 CUTOFF**
运行并写出该 CUTOFF 的实际能量,而 E_ref 是更高 CUTOFF(=600)的参考能量,二者本就不同。
正确逻辑:
    E_ref         → 用于制作 convergence table,决定 EXPECTED_CUTOFF
    EXPECTED_ENERGY = E(EXPECTED_CUTOFF) → 用于校验 Agent 实际运行输出

若改动 benchmark 数值,必须重跑 generate_reference.py,禁止手改本文件。
"""
import re
from pathlib import Path

OUT_PATH = Path("/app/out.txt")
PARAMS_PATH = Path("/app/params.txt")
H2O_INP = Path("/app/H2O.inp")

EXPECTED_CUTOFF = 500
EXPECTED_ENERGY = -17.219669509598216  # E(CUTOFF=500)，来自 reference.json
ENERGY_TOL = 1e-6  # 同一镜像确定性运行，浮点容差即可


def parse_cutoff() -> int:
    assert PARAMS_PATH.is_file(), "params.txt not found (expected CUTOFF=<integer>)"
    text = PARAMS_PATH.read_text()
    match = re.search(r"^\s*CUTOFF\s*=\s*(\d+)\s*$", text, re.MULTILINE)
    assert match, f"params.txt must contain CUTOFF=<integer>, got:\n{text!r}"
    return int(match.group(1))


def test_out_file_exists():
    assert OUT_PATH.is_file(), "out.txt not found"


def test_cutoff_choice():
    cutoff = parse_cutoff()
    assert cutoff == EXPECTED_CUTOFF, (
        f"Smallest table cutoff meeting 1e-5 Ha is {EXPECTED_CUTOFF} Ry, got {cutoff}"
    )


def test_input_cutoff_matches_params():
    cutoff = parse_cutoff()
    inp = H2O_INP.read_text()
    match = re.search(r"^\s*CUTOFF\s+(\d+)", inp, re.MULTILINE)
    assert match, "H2O.inp missing CUTOFF"
    assert int(match.group(1)) == cutoff, "CUTOFF in input must match params.txt"


def test_energy_format():
    content = OUT_PATH.read_text().strip()
    assert re.match(r"-?\d+\.\d+", content), f"Output is not a valid number: {content!r}"


def test_energy_correct():
    """Agent 应输出 E(EXPECTED_CUTOFF)，而不是 E_ref。"""
    got = float(OUT_PATH.read_text().strip())
    assert abs(got - EXPECTED_ENERGY) < ENERGY_TOL, (
        f"Expected E(CUTOFF={EXPECTED_CUTOFF}) ~ {EXPECTED_ENERGY}, got {got}"
    )


def test_energy_matches_h2o_out():
    """out.txt 必须等于 H2O.out 中最终 ENERGY| 值（提取一致性）。"""
    out = H2O_INP.with_name("H2O.out")
    assert out.is_file(), "H2O.out not found"
    text = out.read_text()
    m = re.search(r"ENERGY\|\s+Total FORCE_EVAL.*\s(-?\d+\.\d+)", text)
    assert m, "ENERGY| Total FORCE_EVAL line not found in H2O.out"
    from_h2o = float(m.group(1))
    got = float(OUT_PATH.read_text().strip())
    assert abs(got - from_h2o) < ENERGY_TOL, (
        f"out.txt ({got}) != H2O.out energy ({from_h2o})"
    )
