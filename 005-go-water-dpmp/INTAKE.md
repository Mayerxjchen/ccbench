# 042-go-water-dpmp — INTAKE

## Objective（草案）
用 figshare 发布的 DPMP 训练集（8 体系，14,140 帧）复现 GO–water 界面的
Deep Potential Message Passing (DPMP) 原子间势，并对 hidden 集做静态精度验证
（能量/力 RMSE vs 论文报告模型）。可选: 短程 NVT 动力学稳定性。

## Category / Case kind
- category: `mlp`（registry: supported）
- case_kind: `final_model_retraining`（发布标注数据 → 重训目标模型 → hidden static accuracy）
- capabilities: `model_training` + `hidden_static_accuracy`（`hidden_physical_observable` 可选）
- execution class: `hpc_controller`（参照 034；GPU 训练，agent 控制层）
- 参照案例: 034-ai2kit-water64-end-to-end-potential（HPC + GPU + 门控全套）

## Source materials
| source_id | kind | identity | 状态 |
|---|---|---|---|
| paper-001 | paper | DOI `10.1021/acs.jpclett.5c03713`（JPCL 2026, 17, 1471–1478, Du/Cheng/Tang） | 主论文 PDF 未下载 |
| si-001 | supporting_information | DOI `10.1021/acs.jpclett.5c03713.s001` = `GO–water interfaces/go-water si.pdf`（18p, 文本层可提取） | ✓ 本地 |
| dataset-001 | dataset_metadata | figshare `10.6084/m9.figshare.30472487` v2（posted 2025-10-29, CC BY 4.0）→ `GO–water interfaces/source_data/train_dataset/`（8 体系 14,140 帧, O/H/C, DeepMD raw） | ✓ 本地 |
| config-001 | repository_config | `source_data/input_file/train/train.py`（deepmd_jax DPMP: rcut 6.0, 1M steps, embed[48,48,96]/mp[96,96,96]/fit[96,96,96], s_pref_e 0.02/l_pref_e 1/s_pref_f 1000） | ✓ 本地 |
| model-001 | model_artifact | `source_data/input_file/train/model.pkl`（47MB, DPMP JAX 模型） | ✓ 本地 |
| struct-001 | author_documentation | `source_data/input_file/simulation/initial_structure/{graphene-O12,O25,O50,graphene-water,air-water}` | ✓ 本地 |

## Repository contract
- 仓库: `dftworld2`，Case Factory `python -m dftworld_bench.case_factory render CASE --target dftworld`（读 case-design.yaml，生成 task.toml v1.2 / Dockerfile / .dockerignore / source lock）
- 案例 id: **042-go-water-dpmp**（下一编号，041 之后）
- 参照 034: task.toml schema 1.2, hpc contract `batch_jobs/gpu/artifact_fetch`, allow_internet=false, verifier env 注入 hidden 数据

## 关键开放项（intake 结论）
1. **deepmd-jax runtime 缺口**: 现有镜像 `dftworld-base-deepmd` 只有 `deepmd-kit tensorflow-cpu`（TF），无 `deepmd_jax`/`jax`。论文 train.py 与 model.pkl 均为 DeepMD-JAX。需: (a) 新基础镜像（extend dftworld-base + `pip install deepmd-jax`，需构建授权 + GPU），或 (b) scope 决策。**material decision, 进 construct 前须用户定。**
2. **主论文 PDF** 不在本地（SI 有，主论文 DOI 已知）。复现规格以 SI + train.py + dataset 为准，主论文用于交叉引用。
3. **训练成本**: 1M step 全量训练超长。smoke/formal 需压缩（参照 034: verifier env 控步数）。GPU 训练 + 阈值校准须授权。
4. **许可**: dataset CC BY 4.0（可再分发）✓；code/model 许可未知；paper 为 ACS 订阅。

## 下一步
extract-spec → mlp-reproduction-spec.yaml + source-evidence-map.yaml + reproducibility-assessment.yaml + sources.lock.json（hash_sources.py / validate_spec.py / check_readiness.py）
