# Source Paper Track (025–030) — Case Construction Acceptance Standard

**版本**: v1.1
**日期**: 2026-08-09
**适用范围**: 025-name2opt-so2 ~ 030-react2gibbs-ammonia 六个 benchmark 的构建验收。
**2026-08-15 编号修订**: 六个案例现为 **027–030、039–040**（全局重排），
下文 §025–030 章节名对应新编号：§027 name2opt / §028 name2vib / §029 name2gibbs /
§030 name2file / §039 react2enthalpy / §040 react2gibbs。
**依据**: 用户验收规范 (G0–G8)，基于源论文 (DOI 10.1038/s42004-025-01776-9)。

---

## 0. 总则

每个任务只有同时满足 **8 个 Gate (G0–G8)** 才允许：

```json
"benchmark_valid": true
```

任何一项 FAIL → `benchmark_valid = false`，不得进入 No-Skill / With-Skill 正式实验。

判断一个案例"制定正确"的唯一标准是：

> 能否从原问题出发，在隔离的固定镜像中真实重新得到答案；hidden evaluator 能否从 raw artifacts 独立验证它，同时拒绝伪造答案和典型错误，又接受不同但科学正确的解法？

---

## G0 — Agent 隔离

Agent 容器（`/app`）中只能存在 `public/` 内容 + 运行时软件。**绝不允许**出现：

```
reference/  solution/  tests/  source.lock.json 的答案字段
```

验收命令（agent container 内）：

```bash
find /app -maxdepth 3 -type f
```

必须确认以下文件**不存在**：

```
original_reference.json   regenerated_reference.json
solve.sh                  test_outputs.py
```

**Hard FAIL**：Agent 能 `cat /app/reference/reference.json` → benchmark 立即作废。

> 已由 infra gate G0（`tests/test_workspace_isolation.py`，15 用例全过）保障。

---

## G1 — Source provenance

每个 case 必须能回答四问：

```
这个问题从哪里来？  具体哪个 source experiment？  具体哪个 instance？  答案从哪里来？
```

每个任务必须携带 `reference/source.lock.json`：

```json
{
  "project": "source paper",
  "paper_doi": "10.1038/s42004-025-01776-9",
  "paper_experiment": "name2opt",
  "original_instance_id": "...",
  "original_prompt_source": "...",
  "original_reference_source": "...",
  "source_dataset_version": "...",
  "source_sha256": "..."
}
```

**PASS**：能从 source dataset 找回同一 instance / 同一 query / 同一 method / 同一 molecule-reaction / 同一 original result。

**FAIL**（均不合格）：
- 论文写 A 却换 molecule / 换 method / 编 reaction / 猜测 method。

**关键判定（本 track 已确认）**：
- 恢复的源数据集（commit `a42dd58dba`，40 条）中 `calculator` **只有** `mace_mp (medium-mpa-0)` 与 `TBLite (GFN2-xTB)`，**无任何 DFT (NWChem/ORCA) 实例**。
- 因此 025 的原实例本身就是 MACE —— 为验收标准而"升级"成 DFT 同样违反 G1。
- 镜像 plan 不含 DFT 镜像；025/026/028 → MACE；027/029/030 → xTB。

---

## G2 — Prompt fidelity

`instruction.md` = 原科学问题 + 原参数 + dftworld2 最低 artifact contract。

**可以加**：`Write result.json to /app/result.json.`
**禁止加**：tool-call 顺序 / solution path / 任何 Skill 提示（那是在泄漏 solution）。

Prompt 检查清单：

```
[ ] molecule/reaction 与 source 一致
[ ] temperature 一致
[ ] method 一致
[ ] filename 一致
[ ] scientific objective 一致
[ ] 没有 reference 数值
[ ] 没有 solution-specific 提示
[ ] 没有 Skill-specific 提示
```

---

## G3 — Original reference 原样保存

每题保存 `reference/original_reference.json`，保留作者原 evidence，禁止清洗成 `{"answer": 12.34}`：

```json
{
  "original_tool_calls": [...],
  "original_result": {...},
  "original_structured_output": {...}
}
```

**重要**：original tool-call sequence 是 reference trajectory，**不是 hard PASS 条件**。Agent 走另一条正确路线也应 PASS。

---

## G4 — Regenerated reference 可重现

每题必须有 `reference/generate_reference.py`（或 `.sh`），从 `public input + pinned image` 重新生成 `reference/regenerated_reference.json`，记录：

```json
{
  "image": "...", "image_digest": "...",
  "ase_version": "...", "calculator": "...", "calculator_version": "...",
  "raw_artifact_hashes": {}, "result": {}
}
```

源论文原环境版本参考（不必逐项复制，不同必须记录）：
ASE 3.25.0 · NWChem 7.2.3 · ORCA 6.0 · RDKit 2024.3.5 · TBLite 0.4.0 · mace-torch 0.3.13。

**G4b consistency**：Original ≈ Regenerated（明确 tolerance 内）。差异巨大时**先查** method/version/geometry/weights/temperature/units，**禁止先放宽 tolerance**。

---

## G5 — Oracle 真正执行

`solution/solve.sh` 从 clean container 独立完成整个任务；`solve.sh; pytest -q` 必须 100% PASS。

**禁止**：`cp reference/reference.json /app/result.json`，或 oracle 内 `result = known_answer`。必须走真实 workflow（例 029：四个 species 各自 thermochemistry → 化学计量聚合 → ΔH）。

---

## G6 — Evaluator 六层

```
L1 Artifact     文件存在、可解析、schema/XYZ 可读
L2 Execution    真实 calculator 执行、正常终止、无 NaN、无 fake empty output
L3 Identity     molecule/reaction/method/temperature/filename 正确
L4 Consistency  result.json energy == raw calculator energy（reaction: 报告 ΔH == 各 species 重算 ΔH）
L5 Scientific   几何收敛 / 频率物理有效 / 热化学完整 / 化学计量正确
L6 Reference    Agent result ≈ regenerated reference（最后才比，不是第一手 assert）
```

---

## G7 — Evaluator regression

每道题至少：

```
1 个 oracle fixture            → PASS
3–5 个错误 fixture             → FAIL
1 个 alternative valid fixture → PASS
```

（详见下方各 case 的 fixture 清单。）

---

## G8 — Reproducibility

每个 reference 至少重跑两次。deterministic → 极窄 tolerance；有微小数值差异 → 记录 mean/max deviation，tolerance 基于实测重复性，**禁止先拍 5%**。

---

## 各 case 专用验收

### 025 name2opt  (MACE, 锁 id=5, SO₂)
- molecule 来自 source；original prompt/method 可追溯；**原 method = mace_mp medium-mpa-0 真执行**（修订：源数据无 DFT 实例，见 G1）。
- optimization converged；`optimized.xyz` 存在；composition/chemical identity 保持；energy 与 raw output 一致；geometry 与 regenerated reference 合理一致。
- 必 FAIL：只给初始几何 / 换 method / 未收敛 / 分子断裂原子数错 / 伪造 final energy。

### 026 name2vib  (MACE, 锁 id=6, water)
- molecule/calculator 一致；vibrational 真执行；frequencies 可解析、非空、unit 正确；output 与 raw 一致。
- mode sanity：非线性 N 原子 → 3N-6（water: 3 modes? 不，N=3 → 3N-6=3 个实频 + 平移转动零频），按 backend convention 调整。
- 必 FAIL：空频率数组 / 错 molecule / 错 method / 手写频率 JSON / calculator failure。

### 027 name2gibbs  (xTB, 锁 id=7, CO₂ @800K)
- molecule 正确、temperature 精确匹配、method 正确；thermochemistry 真执行；**交付优化几何 /app/optimized.xyz**，evaluator 从交付几何独立重算 H/S/G（Vibrations + IdealGasThermo，T=800K、P=101325、linear、σ=2、spin=0）；H/S/G 齐全、units 正确；result 与独立重算一致。
- 必 FAIL：温度错 / method 错（含 GFN1-xTB）/ 只返回 electronic energy / 缺 entropy / 伪造 Gibbs / 缺优化几何 artifact / units 错。

### 028 name2file  (MACE, **derived/hybrid adaptation**)
- **DERIVED/HYBRID，非原始实例**：bundled ground_truth.json 无 name2file（save-artifact）实例；molecule/method/optimized energy 取自 dataset id=5（optimization_from_name, SO₂, mace_mp medium-mpa-0），"保存优化几何为 XYZ" 语义取自论文正文 name2file 流程。source.lock.json 明确标注 `instance_kind = derived/hybrid adaptation`，**不得声称是能从数据集找回的同一 name2file 实例**。
- 论文正文：mace_mp geometry optimization + 保存为指定 XYZ；模型曾把 mace_mp 错传给 optimizer（失败-恢复为实验 metric，**不作 PASS gate**）。
- 必满足：original molecule、mace_mp、真优化、**hidden test 对最终 XYZ 重算 MACE forces 且 max‖F‖≤0.01 eV/Å**、converged、指定 filename 精确存在、XYZ 可读、final structure、composition 对、result 与 MACE raw 一致。

### 029 react2enthalpy  (xTB, **paper_text source**, CH₄+2O₂→CO₂+2H₂O @400K)
- **source_type = paper_text**（源数据集 reaction_energy 全是 ΔG，无 ΔH 实例；此实例取自论文正文）。
- **已知科学局限（忠实复现，非缺陷）**：O₂ 源实例未指定 multiplicity，TBLite 默认回退为 singlet（闭壳层）；这是对源实现的忠实复现，但非严格 O₂ triplet 基态热化学。reference 与 evaluator 均按 singlet 复现，与论文 Fig.1 ≈ -12.51 eV 一致。
- 四个 species 独立 thermochemistry，**每个 species 交付优化几何 artifact**（/app/optimized_<NAME>.xyz）；evaluator 从交付几何独立重算各物种 H 与
  `ΔH = H_CO2 + 2·H_H2O − H_CH4 − 2·H_O2`；Agent ΔH == 独立重算 ΔH == regenerated reference；与 paper ≈ -12.51 eV 合理一致。
- 必 FAIL：直接写 -12.51 / 少算 species / 缺优化几何 artifact / 298K / 换 MACE / 化学计量错 / species H 对但算术错。

### 030 react2gibbs  (xTB, 锁 id=32, Ammonia Synthesis @400K)
- **Source Gate**：原 reaction/method/temperature/prompt/species results/最终 ΔG 必须完整；缺任一项不允许构建完成，不得自补。
- 每个 species 独立 thermochemistry、G_i 可追溯；**每个 species 交付优化几何 artifact**（/app/optimized_<NAME>.xyz）；evaluator 从交付几何独立重算各物种 G 与
  `ΔG = Σν_产物·G − Σν_反应物·G`；报告 ΔG ≈ 独立重算 ΔG ≈ regenerated ΔG ≈ original reference。

---

## Alternative Valid Solution Gate

evaluator 不能只让 `solve.sh` 通过。须人工构造 ≥1 种不同组织但科学正确的实现（例：029 oracle = 单脚本连算 4 species；alternative = 4 独立目录 + aggregate.py），两者都应 PASS。否则 evaluator 检查的是"是否复制 oracle"。

---

## 统一验收表

完成任一 025–030 后填写：

| Gate | 标准 | PASS? |
|---|---|---|
| G0 | Agent 看不到 reference/tests/solution | |
| G1 | 原 paper experiment 和 instance 可追溯 | |
| G2 | Prompt 忠实于 source，无答案泄漏 | |
| G3 | Original reference 原样保存 | |
| G4 | Pinned image 可重新生成 reference | |
| G4b | Original ≈ regenerated | |
| G5 | Oracle clean-run 100% PASS | |
| G6a-f | Artifact/Execution/Identity/Consistency/Scientific/Reference | |
| G7a | ≥3 negative fixtures 均 FAIL | |
| G7b | alternative valid solution PASS | |
| G8 | reference rerun reproducible | |

全部 PASS → `benchmark_valid: true`。

---

## 每任务产出 VALIDATION.json

```json
{
  "benchmark_id": "039-react2enthalpy-methane",
  "source": { "paper_verified": true, "instance_verified": true, "prompt_verified": true, "original_reference_verified": true },
  "isolation": { "public_only": true, "hidden_assets_inaccessible": true },
  "reference": { "regenerated": true, "original_consistent": true, "reproducible": true },
  "oracle": { "passes": true },
  "evaluator": { "oracle_passes": true, "negative_fixtures_fail": true, "alternative_solution_passes": true },
  "benchmark_valid": true,
  "agent_smoke_tested": false,
  "ablation_tested": false
}
```

---

## 修订记录

| 版本 | 日期 | 修订 |
|---|---|---|
| v1.0 | 2026-08-09 | 采纳用户 G0–G8 规范；修订 §025 为"原 method (mace_mp) 真执行"（源数据无 DFT 实例，G1 一致）；修订 §029 source_type=paper_text（数据集无 ΔH 实例）；030 锁 id=32 (Ammonia Synthesis, xTB, 400K)。 |
| v1.1 | 2026-08-09 | 案例编号 027–032 → 025–030，目录/task 名去掉 "chemgraph"（025-name2opt-so2 ~ 030-react2gibbs-ammonia）；镜像名去掉 chemgraph（dftworld-base-xtb / dftworld-base-mace）。 |
