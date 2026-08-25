# 019 invalid benchmark — 坏版本冻结(v0)

## 根因(与 017 同构)

```
scf_convergence.txt / instruction.md / tests 的 E_ref = -17.219495884120 Ha
```

这个 E_ref **实际是 CUTOFF=200 的能量**,却被用作"CUTOFF 固定 400 Ry"下的收敛参考。

019 的 H2O.inp 固定 CUTOFF=400。用最终评测镜像 `dftworld-base-cp2k` 实跑:

| 能量 | 值 (Ha) |
|------|--------:|
| E(CUTOFF=200) | -17.21949588411987 ← 019 的"E_ref"其实是这个 |
| E(CUTOFF=400, EPS_SCF=1E-6) | -17.21967528735091 ← Agent 固定 CUTOFF=400 真实收敛值 |
| 两者之差 | 1.794e-4 Ha |

而 tests 要求 `|out.txt - E_ref| < 1e-4`。Agent 固定 CUTOFF=400,无论 EPS_SCF 多紧,
能量都收敛到 `≈ -17.21967528735`,与错误的 E_ref 差 1.79e-4 **恒大于 1e-4** → 永远 FAIL。

## 证据:Agent 执行完全正确

- 读 scf_convergence.txt → 选 `EPS_SCF=1.0E-6`(表里"meets target"的最粗值)✓
- params.txt = `EPS_SCF=1.0E-6` ✓
- H2O.inp 改成 `EPS_SCF 1.0E-6` ✓
- CP2K 实跑 → out.txt = `-17.219675287350910`(= E(CUTOFF=400) 收敛值)✓
- 能量提取与 H2O.out 一致 ✓

失败完全由 benchmark 自身 E_ref 与任务固定 CUTOFF 不自洽导致,非 Agent 错误。

## 另外的问题:scf_convergence.txt 表格数值是编造的

```
1.0E-4 → -17.219120000000    ← 疑似占位符,尾数 000000
1.0E-5 → -17.219480000000
1.0E-6 → -17.219495884120  ~0  ← 直接复用了错误的 E_ref
```

真实 EPS_SCF sweep(固定 CUTOFF=400)下,不同 EPS_SCF 的能量应围绕
`≈ -17.21967528735` 收敛,而不是 `-17.219495884120`。

## 三类问题清单

```
Bug 1  E_ref 数据错误:CUTOFF=200 的能量误标为 CUTOFF=400 收敛值
Bug 2  scf_convergence.txt 表格数值非真实 sweep(编造/占位)
Bug 3  tests 用 E_ref 校验 Agent 实际输出 → 混淆 E_ref 与 E_selected_cutoff
```

修复方式:与 017 完全一致,用最终镜像实跑 EPS_SCF sweep,重新生成 reference。

## Agent 运行档案

```
reward=0.0, ok=false, tool_calls=19, tokens=95869, elapsed=138.9s
```
