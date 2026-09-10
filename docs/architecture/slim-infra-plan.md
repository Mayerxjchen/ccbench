# bench_infra 精简计划与验收

日期：2026-09-11。负责人：Astra 规划、指挥与验收，Luna 实施，独立 reviewer 对抗审核。

参考 dftworld2 commit `945a90c6d34cb5ef16f3e6c78fe5d2ddac7e983d` 的案例目录、共享基础镜像、单一 eval 入口与可见运行记录。其 agent loop 来自 pagentv4；本项目继续使用已有 Claude Code Candidate 与独立 Verifier。

## 实施边界

1. 完整归档当前代码、未提交改动和历史科学资产；归档不包含 `.env`、虚拟环境与本地 runs。
2. 主仓只承担外部论文案例运行。移出旧案例生成、portfolio、ablation、历史科学案例及专属测试；通用测试用最小合成 fixture 验证边界。
3. 保留 Candidate/Gateway、独立 Verifier、公开输入打包、提交冻结、Local/HPC operator、import/resume、累计预算、消息与结果记录和现有 Formal 内容绑定。
4. 统一用户入口，保留 `uv run python eval.py <case路径>` 与 `bench run`；已有 go-water 薄入口继续工作。配置、case 的科学内容与当前镜像可复用。
5. 删除无调用者模块和失效入口，更新依赖、CI 与目录说明。模块数量以实际依赖为准，不将复杂代码简单挤进少数文件。

历史恢复包：`/Users/xjchen/bench/archive/20260911-pre-slim/`。

| 恢复包 | SHA-256 |
| --- | --- |
| history.bundle | 4494a05dd7d1107623670008874652d4d12d1c7432a4b232606c7e36e01a171c |
| worktree.tar.gz | dda39da922ec4a66fd16ae4cb68bb23758b889af2b7d62bbb9a296814bc1b66c |

## 验收门禁

| 门禁 | 要求 | 结果 |
| --- | --- | --- |
| 恢复 | Git bundle 有效，归档抽样恢复字节相同 | PASS：bundle verify 与代码、文档、模型抽样 cmp |
| 目录 | 主仓无内置旧科学案例/模型/历史实验；活跃导入无归档依赖 | PASS：源码树约 293 MB → 36 MB，不计 Git、虚拟环境、runs 和缓存 |
| 最短入口 | 外部 case 路径、用户 config、go-water wrapper 可用 | PASS：eval.py 缩为 39 行；CLI/config 回归及两处入口检查通过 |
| Candidate | 公开输入 allowlist，私有 verifier/宿主目录不可读，无 Docker socket | PASS：packager/隔离合同测试及实际 Docker Candidate 检查 |
| Verifier | 独立断网容器，仅冻结提交与私有验证材料；篡改拒绝 | PASS：实际容器正例/负例；签名回执验证通过，修改结果后拒绝 |
| Local 闭环 | Candidate 产物 → 冻结 → verifier → 结果与消息记录 | PASS：真实 CC + Gateway 容器、本地模拟模型两轮工具调用，同一产物封存后独立验证通过 |
| HPC 续跑 | request → operator output/receipt → import → 原会话 resume；重放拒绝 | 离线合同 PASS；本轮未重新连接 IKKEM/CompShare 运行科学任务 |
| 预算 | 跨阶段累计，配置/快照/计量篡改拒绝，中断保留计量 | PASS：保留并运行当前 pilot/mvp 的预算、续跑、中断测试 |
| Formal | case/config/profile/image/verifier 绑定不因精简而降低 | 回归 PASS；合成 canary 不构成科学案例 Formal 资格 |
| 外部五案例 | 检查 suite/export/readiness；尚缺科学材料的案例继续准确报告未就绪 | PASS：5/5 公开导出不含 verifier/reference/solution；001 READY，002–004 CASE_NOT_READY，005 DRAFT_NOT_READY |
| 回归与发布 | 核心测试通过，CI 同步；提交中无凭据、缓存或本地运维账本 | 本地 900 passed、6 skipped；冻结依赖安装通过；CI 工作流已同步，远端执行结果另行确认 |

测试区分离线 Mock、实际 Docker 和真实算力证据。离线调度闭环不宣称远端科学任务通过，签名与摘要不替代科学 reference。

## 对抗审核与证据

- 独立 reviewer 最终判定 PASS，无剩余 P0/P1；独立核心 targeted 395 项通过，主线程最终完整回归 900 项通过、6 项跳过。
- 否决了将现行预算/隔离测试随旧框架一起删除的初稿，恢复核心测试，用外部合成 case 替换旧科学 fixture。能力迁移见 `tests/TEST_CAPABILITY_MAP.md`。
- 否决无效案例模板，替换成符合当前 schema 的可复制整数求和案例；正确、错误及布尔值冒充整数三种结果均有测试。
- 清理旧案例 ID 默认查找、归档 evidence 默认路径和旧命名环境变量，避免依赖已移出的资产。
- 修正 runtime catalog 中过期的 Candidate 镜像摘要，与当前镜像及本机资格记录一致。发行身份回归不读取用户的本地资格状态，避免把开发者机器状态带入其他用户的默认测试。
- 实际 Docker 验收复用已有镜像，未新建镜像；运行结束后无本轮容器残留。模型端点为本地模拟服务，不能据此宣称 Nonelinear 真实模型或网络稳定性通过。
- 本地证据保留于忽略提交的 `runs/slim-20260911/`：`candidate-loop/`、`candidate-verifier-chain/`、`template-docker.json`。完整消息位于 Candidate workspace 之外，`messages.jsonl` 为只读普通文件。
- 回归存在 4 条 Python 多线程 fork 弃用警告，不影响本次测试通过；未将跳过项计为通过。

## 非阻断后续项

通用整数样例当前复用已注册的 `matclaw-cips-v1` Verifier profile，镜像包含样例不需要的 DeepMD 依赖，尚非最小体积。后续可为轻量 Python/ASE 镜像补齐正式资格再切换默认 profile；本轮直接使用 Python/ASE 的 Docker canary 不替代此资格流程。
