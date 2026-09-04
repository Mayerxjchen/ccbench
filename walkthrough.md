# Gate A1 架构冻结与契约闭环验收总结 (Walkthrough)

## 状态判定
```text
Gate A1 = PASS (加固全部完成，8 项负面契约探针全面通过)
Gate A2–F = BLOCKED (未配置任何真实 CompShare 凭证，未创建真实云实例，未构建远程镜像)
```

根据维护者原则，本阶段恪守“**修完 Gate A1，再接触真实 CompShare**”，在保留 `ComputeProfile → ComputeRouter → RoutedDriver` 路由架构、CLI-first 驱动与两阶段结算的前提下，完成了全部深度加固与安全契约闭环。

---

## 一、完成的 5 个原子提交 (Atomic Commits)

| 序号 | 提交哈希 | 对应阶段 | 核心改动说明 |
| :--- | :--- | :--- | :--- |
| **Commit 1** | `099a1b4` | **P1** | 引入 `RuntimeStatus` 状态机与 `qualified_capabilities()` 过滤，真实锁文件置为 `UNBUILT` (`image_id: null`)，未构建运行时不可解析、不可提交；消除伪 SHA-256 镜像标识。 |
| **Commit 2** | `5f9f605` | **P2, P3** | 实现与 Client Token 解耦的 `trusted_freeze` 与 `trusted_teardown`；两阶段结算状态机（`ACTIVE -> SETTLING -> TEARDOWN_FAILED / SETTLED`）；实例销毁失败记入 `orphan-ledger.jsonl`；启动期 `reconcile_and_recover()` 悬挂实例对账与 fail-closed 守护。 |
| **Commit 3** | `64a9202` | **P4** | 升级 Receipt Schema（添加 `evidence_root`、`evidence_files`、`audit_log`、`signature`）；`GatewayAudit` 添加 `flock + fsync`；实现 Ed25519 签名与校验、证据文件路径遏制（防 `..`/绝对路径越界）与哈希链完整性验证。 |
| **Commit 4** | `f84e248` | **P5, P6** | CompShare 实例强注入 `mlffbench-{run_id}-worker` 确定性归属标记；安全终态严格限定为 `DELETED`/`TERMINATED`（`STOPPED` 强制判定为未终止）；云端查询失败 fail-closed；SiteProfile 驱动 `qualification_policy` 与调度器，消除基于名称猜测调度器的黑魔法。 |
| **Commit 5** | `HEAD` | **P7, P8** | 新增 `test_gate_a1_negative_contracts.py` 全面覆盖 8 个负面契约；将 G16 架构冻结负面契约纳入 `test_release_gates_g0_g15.py`；更新 SKILL 与用户文档；验证全仓测试与工作树洁净。 |

---

## 二、Gate A1 八大负面契约探针 (Negative Contracts Suite)

在 [`tests/hpc/test_gate_a1_negative_contracts.py`](file:///Users/xjchen/bench/mlffbench/tests/hpc/test_gate_a1_negative_contracts.py) 中全部实现并 100% 自动化通过：

1. **Contract 1: Agent 直接声明物理镜像/SIF 路径被拒绝**
   - 证明：Agent 试图绕过能力解析直接传入物理路径时，`CompShareDriver.submit` 强校验拒绝。
2. **Contract 2: 未构建/未验证运行时无法被解析与提交**
   - 证明：当前处于 `UNBUILT` 状态的 `deepmd` 与 `jax` 不出现在 `qualified_capabilities()` 中，尝试解析直接抛出 `RuntimeResolutionError`。
3. **Contract 3: GPU 实例必须具备受信任驱动注入的归属标记**
   - 证明：实例创建时强制注入 `name=mlffbench-{run_id}-worker` 与 `remark=mlffbench:{run_id}:worker`，Agent 无法干涉或伪造。
4. **Contract 4: 结算销毁（Teardown）完全独立于 Token 状态**
   - 证明：Client Token 被撤销或过期后，普通 API 拒绝，但网关受信任看门狗调用 `trusted_freeze` 与 `trusted_teardown` 依然可靠执行，并在审计日志中记录完整生命周期事件。
5. **Contract 5: Zero-Orphan Hard Gate 严格拒绝任何残留与云端异常**
   - 证明：云端存在 `STOPPED`、`RUNNING` 或任何非安全终态实例，或收据孤儿计数 > 0，或云端查询异常时，资格认证一律判定为 FAIL。
6. **Contract 6: 篡改收据后重算 SHA-256 依然无法通过 Ed25519 签名校验**
   - 证明：攻击者修改任何字段并重新计算内容摘要，`verify_receipt_signature` 均准确捕获并阻断认证。
7. **Contract 7: 证据文件路径越界（Path Traversal）被彻底封杀**
   - 证明：收据证据文件试图使用 `..` 相对逃逸或绝对路径时，`check_evidence_containment` 立即拦截并抛出错误。
8. **Contract 8: 严格依据 SiteProfile 声明的探针要求（Required Probe Classes）核验**
   - 证明：SiteProfile 策略声明需要 `["cpu", "gpu"]` 探针时，缺少任一探针即判定为 INVALID，拒绝根据收据现有作业推断策略。

---

## 三、案例合法性与生产入口核验

1. **案例状态核验**：
   - `031-matclaw-cips-eval`: `benchmark_valid = true`
   - `032-matclaw-cips-parity`: `benchmark_valid = true`
   - `033-matclaw-cips-domain-wall-search`: `benchmark_valid = true`
   - `034-ai2kit-water64-end-to-end-potential`: `benchmark_valid = false` (`NOT VALIDATED`)
   - `042-go-water-dpmp`: `benchmark_valid = false` (`NOT VALIDATED`)，且已声明 `runtime.jax`
2. **生产评测入口 (`eval.py`)**：
   - 支持 `--compute-profile`、`--cluster-profile`、`--gpu-site-profile` 与 `--site-profile` 参数。
   - 统一接入 `build_hybrid_stack`，启动前自动执行悬挂实例对账，确保干净基线。

---

## 四、测试与代码规范核验

1. **全仓全量测试套件执行结果**：
   ```text
   ======================= 1371 passed, 2 skipped in 45.15s =======================
   ```
2. **代码风格与换行核验**：
   ```bash
   $ git diff --check
   # 返回码 0，完全干净，无格式或尾随空格问题
   ```
3. **发布门禁 G0–G16 执行结果**：
   ```text
   tests/hpc/test_release_gates_g0_g15.py ................. [100%]
   ============================== 17 passed in 0.68s ==============================
   ```

---

## 五、后续阶段建议（需用户显式授权）

当前 Gate A1 架构冻结与契约已完全加固并达到 PASS 状态。后续阶段按规定保持 **BLOCKED**：
- **Gate A2**：在受管机密存储中配置真实 CompShare 凭据（`COMPSHARE_PUBLIC_KEY`、`COMPSHARE_PRIVATE_KEY`）；
- **Gate B**：构建并注册远程 Docker 镜像（`deepmd`、`jax`），回填生成的正式云平台 `image_id`；
- **Gate C**：执行实机最小 Canary 作业并生成带签名的正式收据；
- **Gate D–F**：启动 034 / 042 案例的真实验证与结算。
