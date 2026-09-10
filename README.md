# bench_infra

计算科学 Agent 评测的通用基础设施。新论文案例放在独立仓库，通过 `bench` 调用本仓库，不需要复制或重建整套 infra。

## 仓库目录

```text
bench_infra/
├── bench/          Python 包与 bench 命令行；Candidate、Verifier、Local/HPC 调度逻辑
├── infra/          通用 Agent profile、runtime catalog 和工具策略
├── runtimes/       Candidate、Gateway、Verifier 镜像配方、环境锁和资格记录
├── schemas/        case、compute request、run record 等数据格式定义
├── scripts/        镜像构建、运行时资格化、发布检查和维护脚本
├── examples/       Candidate 配置、案例模板及 Local/Slurm/CompShare 示例
├── docs/           架构、用户操作、维护和迁移说明
├── tests/          隔离、配置、执行、续跑、HPC、Verifier 和仓库合同测试
├── cases/          旧案例兼容与回归夹具；新论文案例不放在这里
├── benchmark/      历史来源、模型和恢复测试数据
├── maintainer/     维护者私有参考结构和回归资料
├── evidence/       历史运行时资格与验收证据
├── experiments/    现有实验矩阵及兼容配置
├── site-configs/   本地站点配置占位目录；真实凭据不提交
├── .github/        GitHub Actions 自动测试
├── eval.py         仓库内兼容评测入口
├── summarize.py    结果汇总工具
├── pyproject.toml  Python 包、命令和依赖声明
└── uv.lock         可复现 Python 依赖锁
```

论文案例仓库通常只需要：

```text
paper-suite/
├── suite.toml
├── config/bench.toml
├── eval.py
└── cases/
    └── 001-case-name/
        ├── case.toml
        ├── task.md
        ├── input/
        └── verifier/
```
