# MLFFBench 任务总览

本仓库是面向计算化学与机器学习势能建模（MLFF）端到端真实任务的 AI Agent Benchmark。

当前仓库已完成案例精简，**专注于 5 个长周期科学计算与模型开发核心案例**（HPC Controller Cases，目录位于仓库根目录）。每个任务均采用契约化结构，包含 `task.toml`（契约配置）、`instruction.md`（被测 Agent 指令）、`public/`（公开输入数据）、`tests/`（独立密封验证器）以及求解参考。

运行方式详见 [README.md](README.md)：

```bash
uv run python eval.py 001-matclaw-cips-active-distillation
uv run python eval.py --all
```

---

## 核心案例清单

共 **5** 个端到端长周期科学案例。

| 编号 | 任务目录 | 科学领域与方法 | 运行要求 | 状态 |
|------|----------|----------------|----------|------|
| 001 | [001-matclaw-cips-active-distillation](001-matclaw-cips-active-distillation/) | MatClaw CIPS 势能主动学习蒸馏（DeePMD 2.2.11 + LAMMPS） | HPC / GPU | `benchmark_valid=true` |
| 002 | [002-matclaw-cips-curie-temperature](002-matclaw-cips-curie-temperature/) | MatClaw CIPS 居里温度分子动力学估计 | HPC / GPU | `benchmark_valid=true` |
| 003 | [003-matclaw-cips-domain-wall-search](003-matclaw-cips-domain-wall-search/) | MatClaw CIPS 电场与温度自适应畴壁搜索 | HPC / GPU | `benchmark_valid=true` |
| 004 | [004-ai2kit-water64-end-to-end-potential](004-ai2kit-water64-end-to-end-potential/) | 64 水分子端到端机器学习势能开发（CP2K AIMD + ai2kit） | HPC / CPU+GPU | 建设中（`benchmark_valid=false`） |
| 005 | [005-go-water-dpmp](005-go-water-dpmp/) | 氧化石墨烯–水界面 DPMP 势函数全流程开发（CP2K + DPMP/JAX） | HPC / CPU+GPU | 草稿阶段（Runnable Draft） |

---

## 案例详细介绍

### 1. 001-matclaw-cips-active-distillation
- **目标**：从 teacher 势能通过主动学习蒸馏高效训练快速的 CIPS DeePMD 势函数模型。
- **输入**：`public/` 中的初始晶体结构与主动学习配置文件。
- **输出**：蒸馏训练得到的冻结模型文件 `cips_distilled.pb` 及主动学习迭代日志。
- **验证**：密封验证器执行能量力精度评测与主动学习收敛轨迹验证。

### 2. 002-matclaw-cips-curie-temperature
- **目标**：基于训练好的 CIPS 势函数，通过分子动力学（MD）多温区升温模拟，从自发极化曲线突变点确定材料的居里温度（$T_c$）。
- **输入**：超胞结构与 MD 模拟参数模板。
- **输出**：极化强度随温度变化的统计数据与居里温度判定报告。
- **验证**：验证温度网格充分性、极化计算准确性及与实验/文献值的置信区间吻合度。

### 3. 003-matclaw-cips-domain-wall-search
- **目标**：在外加电场和温度自适应调节下，搜索并稳定 CIPS 铁电畴壁（Domain Wall）构型。
- **输入**：多畴超胞模型及外场梯度控制参数。
- **输出**：极小能量畴壁原子构型坐标与畴壁形成能。
- **验证**：畴壁构型能量极小性检验及电偶极矩连续性验证。

### 4. 004-ai2kit-water64-end-to-end-potential
- **目标**：从 64 水分子未弛豫构型出发，驱动 CP2K 进行第一性原理分子动力学（AIMD）数据采样，通过 ai2kit 自动化调度进行主动学习迭代与验证，获得可靠的液态水 DeePMD 势函数。
- **输入**：初始构型与 ai2kit 自动化流水线配置模板。
- **输出**：冻结势函数模型与 NVT 径向分布函数（RDF）验证曲线。
- **验证**：隐藏 DFT 测试集能量/力 RMSE 评测及常温常压水密度与 RDF 结构验证。

### 5. 005-go-water-dpmp
- **目标**：复现氧化石墨烯–水（GO–Water）界面 DPMP 机器学习势能全流程：CP2K revPBE-D3 标记、基于 JAX 的 DPMP 模型训练、主动学习数据扩充与静态/动态/物理性质检验。
- **输入**：氧化石墨烯基底构型与初始溶剂化参数。
- **输出**：训练完成的模型权重与界面吸附能验证。
- **验证**：独立验证器比对隐藏的发表数据集基准与界面结构特征。

---

## 运行与契约架构说明

- **执行分类**：所有 5 个案例均声明严格的 `[execution] class = "hpc_controller"` 契约。
- **环境隔离**：被测 Candidate Agent 在安全沙箱容器（`mlffbench-candidate-claude-code-sandbox:v1`）内运行，网络通过宿主双网卡 Sidecar 及 ModelGatewayProxy 严格受控。
- **资源调用**：案例不直接拥有 Docker 镜像或节点权限，由 HPC Dispatcher 与运行时注册表动态解析并调度计算环境。
