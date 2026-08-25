"""019-cp2k-eps-scf 测试。

数值来源:``reference/reference.json``(由 ``generate_reference.py`` 用最终评测镜像
``dftworld-base-cp2k`` / CP2K 2025.2 / CUTOFF=400 / REL_CUTOFF=40 实跑生成)。

设计要点:
    - 任务要求 Agent 用**选定的 EPS_SCF** 运行并写出该 EPS_SCF 的实际能量。
      因此校验 out.txt 对比 ``EXPECTED_ENERGY``(=E(选定的 EPS_SCF)),而不是 E_ref。
    - 正确答案是"最粗(最大数值)仍满足 target"的 EPS_SCF,不是固定的 1E-6。
      本环境实测所有候选都满足 → 最粗 = 1.0E-4。

若改动 benchmark 数值,必须重跑 generate_reference.py,禁止手改本文件。
"""
import re
from pathlib import Path

OUT_PATH = Path("/app/out.txt")
PARAMS_PATH = Path("/app/params.txt")
H2O_INP = Path("/app/H2O.inp")

EXPECTED_EPS = 1.0e-4
EXPECTED_ENERGY = -17.219675276173536  # E(EPS_SCF=1.0E-4)，来自 reference.json
ENERGY_TOL = 1e-6  # 同一镜像确定性运行，浮点容差即可


def parse_eps_scf() -> float:
    assert PARAMS_PATH.is_file(), "params.txt not found (expected EPS_SCF=<value>)"
    text = PARAMS_PATH.read_text()
    match = re.search(r"^\s*EPS_SCF\s*=\s*([\d.Ee+-]+)\s*$", text, re.MULTILINE)
    assert match, f"params.txt must contain EPS_SCF=<value>, got:\n{text!r}"
    return float(match.group(1))


def test_out_file_exists():
    assert OUT_PATH.is_file(), "out.txt not found"


def test_eps_scf_choice():
    eps = parse_eps_scf()
    assert eps == EXPECTED_EPS, (
        f"Coarsest EPS_SCF meeting 1e-5 Ha target is {EXPECTED_EPS:.1e}, got {eps}"
    )


def test_input_eps_matches_params():
    eps = parse_eps_scf()
    inp = H2O_INP.read_text()
    match = re.search(r"^\s*EPS_SCF\s+([\d.E+-]+)", inp, re.MULTILINE)
    assert match, "H2O.inp missing EPS_SCF"
    assert float(match.group(1)) == eps, "EPS_SCF in input must match params.txt"


def test_energy_format():
    content = OUT_PATH.read_text().strip()
    assert re.match(r"-?\d+\.\d+", content), f"Output is not a valid number: {content!r}"


def test_energy_correct():
    """Agent 应输出 E(选定的 EPS_SCF)，而不是 E_ref。"""
    got = float(OUT_PATH.read_text().strip())
    assert abs(got - EXPECTED_ENERGY) < ENERGY_TOL, (
        f"Expected E(EPS_SCF={EXPECTED_EPS:.1e}) ~ {EXPECTED_ENERGY}, got {got}"
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
