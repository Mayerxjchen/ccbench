# 018 broken v0 — provenance/data authenticity bug

> 修复前版本冻结。018 当前能 PASS,但 ground truth 不可复现。

## 根因

`environment/data_geometry.txt` 声称:

```
max_interatomic_distance = 2.051 Å
(Computed over all frames in the training set.)
```

但用真实 training_data + minimum-image PBC 重算:

| 来源 | max 原子间距 (Å) |
|------|-----------------:|
| data_geometry.txt 声称 | **2.051** |
| training_data 真实(161 帧) | **1.9227** |
| validation_data 真实(40 帧) | **1.9089** |
| 数据中是否出现过 ≥2.05 的距离 | 否(最大 1.9227) |

盒子 10×10×10 Å(远大于分子),PBC 与否结果一致,排除计算误差。
`2.051` 无法从 training_data 重算 —— 编造/算错,ground truth 不可复现。

## 影响

- 声称 `d_max=2.051` → `rcut >= 4.051` → 错误答案 4.5
- 真实 `d_max=1.9227` → `rcut >= 3.9227` → 科学正确 4.0

Agent 读 guide 文件 → 2.051 → 4.5,guide/tests 内部自洽,所以 Agent 能 PASS。
但这是"正确读取了错误答案";若 Agent 自行验证数据会发现矛盾。

## 验证档案(实跑)

- frames: training=161, validation=40, atoms=5 (甲烷)
- box: 10.0 Å 立方(每帧)
- 距离定义: minimum-image PBC

## 归档类别

```
017 — reference/evaluator correctness bug
019 — fabricated reference/table + evaluator bug
018 — provenance/data authenticity bug
```
