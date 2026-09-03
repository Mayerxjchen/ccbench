# Scientific Benchmark Case Builder — MVP 优化计划

日期：2026-09-03  
计划模型：GPT-5.6 Sol  
目标 skill：`scientific-benchmark-case-builder-portable`

## 1. 目标

把 skill 优化为：Agent 输入论文、SI、仓库、数据或模型后，可以快速构造一个
**真正能够启动首次长案例 Discovery run 的 MVP 案例**，而不需要先投入正式
reference、双重阈值校准和 release 级 verifier 的成本。

MVP 的核心定义：

> Candidate 可以被正确打包并启动；长任务有明确执行路径；submission 能被
> 独立 verifier 读取；verifier 总能产出标准结果；失败能区分案例、运行时、
> 基础设施和 Agent 问题；案例仍明确保持 `benchmark_valid=false`。

首轮优化不追求“自动生成 benchmark-valid 案例”，只追求：

```text
source -> coherent design -> executable candidate bundle
       -> runnable Discovery -> classified evidence
```

## 2. MVP 边界

### MVP 必须完成

- evidence-backed task/design；
- 正确的 public/hidden 边界；
- 可由真实 packager 生成的 Candidate bundle；
- 候选输入路径与 instruction 完全一致；
- 与仓库 harness 兼容的 `tests/test.sh`；
- 至少一个真正读取 sealed submission 的 verifier；
- verifier 无论 PASS/FAIL 都写标准 `/logs/verifier/result.json`；
- empty、伪造 manifest、缺模型、断裂 active loop 等负例可执行；
- local/runtime smoke 与正式长计算分离；
- real Discovery 的启动命令、停止条件和 evidence record；
- deterministic failure classification；
- `benchmark_valid=false`，reference/threshold 可以是 planned/deferred。

### MVP 暂不要求

- 正式 expert solution；
- 完整 alternative scientific implementation；
- 双 reference 重跑；
- frozen scientific thresholds；
- release 级 hidden V4/V5/V6 科学验证；
- evidence 对象远程上传、restore 和 G0–G12 全闭环；
- `experiment-handoff` 或正式 ablation。

这些保留在 PROMOTE 后阶段，不能反向阻塞首次 Discovery。

## 3. 当前主要缺口

根据 Luna 留一测试，当前 skill 能生成结构完整的设计骨架，但存在以下阻断：

1. `runnable_draft` 没有可执行定义；树和 YAML 合法就可能被声明为 runnable。
2. 模板没有 harness-compatible verifier 主体和入口。
3. 构造期自检容易被误当成 submission verifier。
4. validators 没有统一的 fail-fast 入口，Agent 会用 YAML 语法解析替代语义校验。
5. cross-layer checker 不检查实际打包后的路径、submission root、held-out 所有权和
   instruction/contract 一致性。
6. fixture matrix 可以只有说明文件，却仍表现为 coverage 完整。
7. MLP capability 容易误标，例如 teacher-potential labeling 被写成
   `dft_dynamics`。

## 4. 实施策略

采用三层漏斗，避免一次性重写整个 skill：

| 层级 | 目标 | 允许状态 |
|---|---|---|
| L0 Scaffold | 文件和来源结构可解析 | `draft` |
| L1 Discovery MVP | Candidate、runtime smoke、verifier、结果分类可执行 | `runnable_draft` |
| L2 Release | reference、hidden science、threshold、fixtures、evidence 全闭环 | `benchmark_valid` |

本轮只交付 L1，并保证 L2 既有规则不被削弱。

## 5. 工作包

### WP0 — 冻结 MVP 合同（半天）

新增 `references/common/mvp-runnable-draft.md`，只定义 L1 的必要条件和禁止声明。
更新 `SKILL.md` 的 `scaffold`、`construct`、`validate`、`discovery` 路由：

- `scaffold` 只能产生 `draft`；
- 只有新的 MVP gate 全部通过，才可产生 `runnable_draft`；
- `runnable_draft` 不等于 verifier/science/release 完成；
- 第一次 real run 使用 `discovery` profile，而不是 `formal` profile；
- reference、hidden static accuracy 和 thresholds 在 Discovery MVP 中可以明确
  `planned/deferred`。

验收：skill 中只有一处权威定义 `runnable_draft`，其他 reference 链接到它。

### WP1 — 提供可运行的 verifier 最小骨架（1 天）

在 MLP template 中增加：

```text
tests/test.sh
tests/verifier.py
tests/test_verifier_contract.py
tests/fixtures/negative/{empty,broken-lineage,missing-model}/
tools/validate_submission_manifest.py
public/submission-schema.json
```

要求：

- `tests/test.sh` 只承担 verifier 容器入口，不运行 case-construction 自测；
- 使用仓库 common launcher，或严格生成相同的标准 `result.json`；
- verifier 从 `/submission` 或 `/app` 读取 sealed submission；
- 所有路径必须相对 sealed root，拒绝绝对路径、`..`、symlink、hardlink；
- 最小检查覆盖 V0 identity、V1 provenance、V2 model artifact、V3 workflow
  lineage、C-V7 job/runtime receipts、C-V8 integrity；
- V4/V5/V6 在未实现时必须显示为 `deferred`，不能假 PASS；
- case-specific science 通过小型 hook 函数补充，模板不能假装能够自动验证任意 MLP。

验收：在 verifier 的真实 mount layout 中，empty 和 broken submissions 都产生合法
`AGENT_FAILURE` result；一个结构完整的 synthetic fixture 能跑完整技术链，但其结果
明确标为 Discovery diagnostic，不代表 benchmark science PASS。

### WP2 — 新增 `check_discovery_runnable.py`（1 天）

这是 MVP 的唯一派生入口。输入 CASE，执行：

1. `validate_case.py`；
2. category spec validation/readiness；
3. `derive_verifier_plan.py`；
4. `generate_fixture_matrix.py`；
5. `check_draft_consistency.py`；
6. 使用仓库真实 `CaseSpec` 和 `package_candidate()` 打包到临时目录；
7. 校验 bundle 中的路径、instruction、input manifest 和 submission schema；
8. 在真实 `/tests`、`/submission`、`/logs/verifier` 语义下执行 verifier contract
   smoke；
9. 校验标准 `result.json` schema 和 failure attribution；
10. 检查 `benchmark_valid=false`、reference/threshold 未伪造完成。

输出机器可读：

```json
{
  "mvp_runnable": false,
  "checks": {},
  "blocking_errors": [],
  "deferred_release_work": []
}
```

只有该脚本可以把状态派生为 `runnable_draft`。任何 validator 缺依赖、未运行或返回
非零都必须阻断；Ruby/YAML 语法检查不得替代语义 validator。

验收：把 Luna 测试产物原样作为 regression fixture 时，必须准确报告：

- spec 的 `unknown + concrete value` 错误；
- instruction 中 `public/...` 与实际 bundle 路径不一致；
- submission root/`final/` 不一致；
- verifier mount/entry/result 闭环缺失。

### WP3 — 加强跨层一致性（1 天）

扩展 `check_draft_consistency.py`，避免依赖脆弱的全文自然语言解析。新增机器可读字段：

- `public/input-manifest.json.files[*].candidate_path`；
- `case-design.yaml.submission.root`；
- `case-design.yaml.validation_sets`：
  `candidate_generated | verifier_hidden | expert_calibration`；
- `case-design.yaml.metric_contract`，含 operator（`<`、`<=`、`>`、`>=`）；
- `verifier-plan.yaml.submission_root` 和 `result_path`；
- `candidate_visible` 必须由实际 packager manifest 反向验证。

检查以下不变量：

- prompt 使用的输入路径存在于打包后 bundle；
- task、instruction、submission schema、verifier 使用同一 submission root；
- held-out 数据的创建者、可见性和评分角色唯一；
- metric 名称、单位、operator 在 prompt/design/verifier 中一致；
- workflow capability 与 label source 一致：teacher inference 不是 DFT；
- `CONTRACT.md` 若声明 candidate-visible，就必须实际进入 bundle，否则删除该声明。

验收：每一种跨层漂移都配一个最小 regression fixture，不写只匹配措辞的测试。

### WP4 — 区分构造自检和运行 verifier（半天）

目录职责固定：

```text
builder-tests/ 或 portable package tests/  # 检查案例构造质量
tests/test.sh                              # 只用于 hidden verifier runtime
tools/                                    # Candidate 不可见的开发工具
```

如果仓库不接受新增 `builder-tests/`，构造自检留在 portable skill 的 `tests/`，不要
放进生成案例的 runtime verifier 入口。

验收：在 case 根运行测试和在 `/tests` mount layout 运行测试得到一致、可解释的结果；
任何脚本都不依赖“它恰好从案例根目录执行”。

### WP5 — 建立一条 `mvp` 快捷命令（半天）

在现有 command model 中新增或明确组合模式：

```text
/build-scientific-benchmark-case mode=mvp category=mlp
```

它按顺序执行 intake → extract-spec → design → scaffold → minimal construct → MVP
validate，但在以下位置暂停：

- 需要选择不同科学目标；
- 下载/许可证不清；
- container build；
- real scheduler submission；
- 昂贵训练、DFT、MD；
- 破坏性覆盖。

默认输出：案例树、`MVP-READINESS.json`、`DISCOVERY-RUNBOOK.md`。不要生成 ablation
或 release packet。

验收：一次调用能把完整输入推进到 `draft` 或 `runnable_draft`；若阻断，给出一个
短且可执行的 blocker 列表，而不是留下表面完整的案例。

### WP6 — Forward test（1 天）

使用新的独立 Luna 测试，采用另一组留一设计：

- held-out target：032；
- 可参考：031、033、034；
- 禁止读取 032 和本次修复结论；
- 输出隔离到 `experiments/case-builder-luna-v2/`；
- 不允许网络、container build 或 real HPC；
- 首次完成冻结，不进行 score-driven retry。

必须通过：

1. `check_discovery_runnable.py`；
2. semantic spec validator；
3. actual Candidate packaging；
4. actual verifier mount smoke；
5. empty/broken submission negative tests；
6. common result schema；
7. `benchmark_valid=false`；
8. 没有 forbidden-path access 或未授权 package fetch。

目标分数：≥80/100，且 Runnable-Draft mechanics ≥21/25、Verifier ≥14/20。

## 6. 建议实施顺序

```text
Day 1 AM  WP0 MVP contract
Day 1 PM  WP1 verifier skeleton
Day 2 AM  WP2 runnable checker
Day 2 PM  WP3 cross-layer checks
Day 3 AM  WP4 separation + WP5 command
Day 3 PM  regression suite
Day 4     independent Luna forward test + one focused correction
```

预计 3–4 个工程日可以得到可用 MVP。若时间只有 1–2 天，优先完成 WP1、WP2、
WP3；没有这三项，不应继续使用 `runnable_draft` 这个状态。

## 7. 测试矩阵

| Fixture | 预期 |
|---|---|
| 合法 scaffold、无 verifier | `draft`, blocked |
| verifier dry-run 永远 PASS | blocked |
| test.sh 不写 result.json | blocked |
| instruction 使用打包后不存在的路径 | blocked |
| submission root 不一致 | blocked |
| held-out 同时声明 Candidate 与 hidden 所有 | blocked |
| spec 语法合法但 claim semantics 错 | blocked |
| empty submission | verifier 产生 AGENT_FAILURE |
| broken active lineage | verifier 产生 AGENT_FAILURE |
| runtime/site 未授权 | runnable 文件可完成，但 Discovery execution blocked |
| reference/threshold planned | 不阻断 Discovery MVP |
| benchmark_valid 手写 true | hard fail |

## 8. Definition of Done

本轮 skill 优化完成必须同时满足：

- 从一个真实长案例源包生成新案例；
- 真实 packager 输出与 instruction 一致；
- verifier 在 harness mount 语义下运行；
- 标准结果总能生成并通过 schema；
- 至少三个负例被真实执行并正确拒绝；
- real Discovery 命令无需人工修目录或入口；
- run record 可以进入 failure classifier；
- 所有未完成科学工作显示为 deferred/open，不影响首次 Discovery；
- `benchmark_valid` 仍只能由 release derivation 写入；
- portable package 的原有测试通过，并新增 Luna failure regressions；
- 独立 forward test 达到上述目标分数。

## 9. 非目标与防止范围膨胀

第一轮不要：

- 自动生成 031 等级的 500–1500 行科学 verifier；
- 为所有 MLP framework 建通用模型加载器；
- 自动决定科学阈值；
- 自动执行昂贵 reference；
- 把 034/042 的每个历史故障都写进 SKILL.md；
- 同时重构 dftworld harness、HPC gateway 和 evidence storage。

MVP 只需要证明“案例可启动、submission 可评分、失败可归因”。真实 Discovery
结果再决定 REJECT、REFINE 或 PROMOTE，以及是否值得投入 release 级工程。

## 10. 第一批具体改动清单

建议首个实现 PR 只包含：

1. `mvp-runnable-draft.md`；
2. verifier/test.sh/submission-schema template；
3. `check_discovery_runnable.py`；
4. Luna v1 四类 regression fixtures；
5. `check_draft_consistency.py` 的 bundle/submission/held-out 检查；
6. SKILL.md 路由和状态规则更新；
7. portable tests；
8. Luna v2 forward-test protocol。

其他 reference/release 增强全部拆到后续工作，避免延迟 MVP。
