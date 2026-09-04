# Gate A1 架构加固与 Gate B/C 离线准备实施计划 (Implementation Plan)

## 状态总览
按照“**修完 Gate A1，再接触真实 CompShare**”原则，本计划在保留现有 `ComputeProfile → ComputeRouter → RoutedDriver` 路由架构、CLI-first 驱动与两阶段结算（Settlement）的基础上，针对 Gate A1 进行深度加固与契约闭环。

```text
Gate A1: BLOCKED (加固整改中，直至 P1–P7 全部通过)
Gate A2: 已完成只读预检证据，但不是云资源授权
Gate B/C: BLOCKED (本轮只做仓库内离线准备；禁止云命令、创建实例、构建远程镜像)
Gate D–F: BLOCKED

本轮追加的 scope freeze 见
[`ADR-2026-09-04-COMPSHARE-IMAGE-A-SCOPE.md`](docs/architecture/ADR-2026-09-04-COMPSHARE-IMAGE-A-SCOPE.md)。
首轮 CompShare Image A 只覆盖 031–033 的 `runtime.matclaw-gpu`；034 和
042 明确排除。此前文档中把 034 GPU 列入 Image A 的表述已撤回。

Failover 只能由 operator 在结束并结算旧 run 后，人工选择已经
qualification 的备用 profile 并启动新 run；活动 RunLock 不得漂移。
```

最终目标：
1. **Agent 只能选择经过正式 qualification 的 runtime**（未构建/未验证一律不可见、不可解析、不可提交）；
2. **任何关闭路径都不会静默遗留计费实例**（Token 过期/撤销不影响可信销毁，崩溃自动恢复）；
3. **Receipt 不能靠手写字段或自算 SHA 获得 PASS**（绑定不可篡改的 `GatewayAudit` 哈希链与 Ed25519 数字签名）；
4. **CPU/GPU qualification 要求来自可信 SiteProfile**（拒绝凭收据内容反推要求，调度器由 SiteProfile 严格确定）。

---

## User Review Required

> [!IMPORTANT]
> 1. **状态修正**：撤销此前提前宣称的 Gate A1 PASS，正式将当前状态设定为 `Gate A1 = BLOCKED`，直至本计划所有负向门禁与自动化契约测试全部绿灯。
> 2. **5 个独立提交拆分**：本轮改动将拆分为 5 个原子 Git Commit，按安全性质独立回滚与审计，绝不污染真实云端运维阶段。
> 3. **签名信任锚机制**：采用 `Ed25519` 数字签名对 Qualification Receipt 进行防伪封存。公钥存放在 `SiteProfile` / Trust Store，私钥严格留在 Maintainer 受信端（不进仓库、不暴露给 Agent）。

---

## Open Questions

无。所有架构与工程契约已在用户指令中明确，无需额外澄清。

---

## 详细实施步骤 (P0–P8)

### P0：冻结修复基线与工作树分类

1. **备份基线**：已将当前完整 diff 保存至受管 scratch 路径 `baseline_pre_a1.diff`，记录当前 HEAD 为 `d5643e2`。
2. **修改分类矩阵**：
   * **核心 Infra 代码 (15 个)**：
     - `dftworld_bench/cli.py`
     - `dftworld_bench/hpc/dispatcher.py`
     - `dftworld_bench/hpc/drivers/__init__.py`
     - `dftworld_bench/hpc/drivers/base.py`
     - `dftworld_bench/hpc/drivers/routed.py` [新文件]
     - `dftworld_bench/hpc/drivers/compshare/cli.py`
     - `dftworld_bench/hpc/drivers/compshare/driver.py`
     - `dftworld_bench/hpc/drivers/compshare/instance_manager.py`
     - `dftworld_bench/hpc/gateway.py`
     - `dftworld_bench/hpc/gateway_runtime.py`
     - `dftworld_bench/hpc/production.py`
     - `dftworld_bench/hpc/runtime_resolution.py`
     - `eval.py`
     - `schemas/compute-profile-qualification-receipt.schema.json`
     - `schemas/hpc-site-profile.schema.json`
   * **验证与收据框架 (4 个)**：
     - `dftworld_bench/experiments/compute_profile_qualification.py`
     - `dftworld_bench/experiments/qualification_receipt.py`
     - `examples/hpc/compshare-gpu-site-profile.json` [新文件]
     - `examples/hpc/ikkem-cpu-site-profile.json` [新文件]
   * **Runtime Lock 与 Case 元数据 (5 个)**：
     - `reference/runtime/deepmd-runtime.lock.json`
     - `reference/runtime/jax-runtime.lock.json`
     - `034-ai2kit-water64-end-to-end-potential/benchmark_valid.json` (保持 NOT VALIDATED)
     - `042-go-water-dpmp/benchmark_valid.json` (保持 NOT VALIDATED)
     - `042-go-water-dpmp/task.toml`
   * **测试套件 (9 个)**：
     - `tests/ablation/test_readiness_audit.py`
     - `tests/hpc/test_compshare_driver.py`
     - `tests/hpc/test_compute_profile_qualification.py`
     - `tests/hpc/test_qualification_receipt.py`
     - `tests/hpc/test_release_gates_g0_g15.py`
     - `tests/hpc/test_runtime_resolution.py`
     - `tests/test_mlffbench_cli.py`
     - `tests/hpc/test_compshare_cli_contract.py` [新文件]
     - `tests/hpc/test_routed_driver.py` [新文件]
   * **文档与配置 (6 个)**：
     - `.gitignore`
     - `base-env-build/skills/hpc-submit/SKILL.md`
     - `docs/maintainer/compshare_gpu_images.md`
     - `docs/user/slurm_quickstart.md`
     - `implementation_plan.md` [新文件]
     - `walkthrough.md` [新文件]
3. **状态标记**：明确标记 Gate A1 为 `BLOCKED`，未到 A2 授权禁止进行真实 CompShare 写操作。

---

### P1：重新定义 GPU Runtime Identity (Commit 1)

#### [MODIFY] [runtime_resolution.py](file:///Users/xjchen/bench/mlffbench/dftworld_bench/hpc/runtime_resolution.py)
* **删除伪 digest 派生**：彻底移除对 `(image_id, software_versions, base_image)` 计算 SHA-256 充当 `image_sha256` 的逻辑。严格区分 `image_id`（云平台镜像 ID）、`lock_digest`（lock 文件本身的 canonical hash）和 `qualification_receipt_digest`（收据哈希）。
* **引入 Runtime 状态机**：
  * `UNBUILT`: `image_id` 为 `null`。
  * `BUILT_NOT_QUALIFIED`: 有 `image_id`，但无验证通过的收据。
  * `QUALIFIED`: 绑定合法的 Qualification Receipt 且签名通过。
  * `FAILED`: 资格验证失败。
  * `REVOKED`: 镜像被撤销。
  * 仅 `QUALIFIED` 允许出现在 `qualified_capabilities()`、允许被解析、允许提交、允许写入 RunLock。
* **修改 Capabilities 接口**：统一暴露 `qualified_capabilities()`，移除语义模糊的 `capabilities(resolved_only=True)`。

#### [MODIFY] [deepmd-runtime.lock.json](file:///Users/xjchen/bench/mlffbench/reference/runtime/deepmd-runtime.lock.json) 与 [jax-runtime.lock.json](file:///Users/xjchen/bench/mlffbench/reference/runtime/jax-runtime.lock.json)
* 升级至 `dispatcher-compshare-runtime-lock/v2` schema：
  * 设置 `"image_id": null`，删除假占位符 `img-deepmd-gpu-v1` 与 `img-jax-gpu-v1`。
  * 显式标记 `"qualification": {"status": "NOT_RUN", "receipt_path": null, "receipt_digest": null}`。

#### [MODIFY] [routed.py](file:///Users/xjchen/bench/mlffbench/dftworld_bench/hpc/drivers/routed.py) 与 [gateway.py](file:///Users/xjchen/bench/mlffbench/dftworld_bench/hpc/gateway.py)
* 对齐只调用 `resolver.qualified_capabilities()`。

#### [MODIFY] [driver.py](file:///Users/xjchen/bench/mlffbench/dftworld_bench/hpc/drivers/compshare/driver.py)
* **二次防御**：`CompShareDriver.submit()` 必须硬性断言：
  1. `_resolved_runtime` 存在；
  2. `artifact_kind == "compshare_image"` 且 `provider == "compshare"`；
  3. `qualification_verified is True`；
  4. `image_id` 与收据声明一致。
* **删除后门**：禁止 Candidate 通过 `deepmd@img-xxx` 绕过 Resolver 注入 ImageId。

---

### P2：Token 无关的 Trusted Teardown 与生命周期 (Commit 2)

#### [MODIFY] [gateway.py](file:///Users/xjchen/bench/mlffbench/dftworld_bench/hpc/gateway.py) 与 [gateway_runtime.py](file:///Users/xjchen/bench/mlffbench/dftworld_bench/hpc/gateway_runtime.py)
* 新增 Trusted 控制通道：
  * `gateway.trusted_freeze(run_id)`
  * `gateway.trusted_teardown(run_id)`
  不依赖客户端传递的 Token，只要系统确认 run 所有权，即可无条件触发。
* 状态机流转：`ACTIVE -> SETTLING -> TEARDOWN_FAILED / SETTLED`。
* 异常处理：teardown 失败时自动写入 `orphan-ledger.jsonl`，状态置为 `TEARDOWN_FAILED`。

#### [MODIFY] [dispatcher.py](file:///Users/xjchen/bench/mlffbench/dftworld_bench/hpc/dispatcher.py)
* 重构 `DispatcherSession.close()` 的执行序列：
  ```text
  1. gateway.trusted_freeze(run_id)
  2. gateway.trusted_teardown(run_id)
  3. 确认 settlement 状态为 SETTLED
  4. lease.close() (撤销 token)
  5. 关闭网络连接
  ```
  即使 Token 已经处于 `REVOKED` 或 `EXPIRED` 状态，步骤 1–3 也必须强制执行，消除 `EXPLICIT_REVOKE_SETTLE_CALLS = []` 的安全漏洞。
* **同 Run Replacement**：重新打开同一个 `run_id` 时，先对旧资源执行 `trusted_teardown` 并确认 `SETTLED`，若旧资源无法确认删除，禁止新 Run 启动。

---

### P3：自动 Durable Recovery 接入生产路径 (Commit 2)

#### [MODIFY] [instance_manager.py](file:///Users/xjchen/bench/mlffbench/dftworld_bench/hpc/drivers/compshare/instance_manager.py)
* **消除构造函数副作用**：移除 `__init__` 中的静默联网清理，提供显式受信任入口 `manager.reconcile_and_recover()`。
* **结构化恢复报告**：
  ```python
  @dataclass
  class RecoveryReport:
      recovered: list[str]
      still_active: list[str]
      query_failed: list[str]
      delete_failed: list[str]
      def ok(self) -> bool:
          return not (self.still_active or self.query_failed or self.delete_failed)
  ```
* 只要 `not report.ok()`，必须 Fail-Closed 抛出异常。

#### [MODIFY] [production.py](file:///Users/xjchen/bench/mlffbench/dftworld_bench/hpc/production.py)
* 区分启动模式：
  * `test mode`: 允许 fake CLI。
  * `production mode`: 启动时必须执行 `reconcile_and_recover()` 且返回 `ok()`，否则拒绝进入 `READY`。
  * `offline verification mode`: 禁止一切网络与 CLI 外部调用。

---

### P4：重做 CompShare Qualification Receipt 信任链 (Commit 3)

#### [MODIFY] [compute-profile-qualification-receipt.schema.json](file:///Users/xjchen/bench/mlffbench/schemas/compute-profile-qualification-receipt.schema.json)
* 严格规范 Schema，必需字段覆盖：`schema_version`, `kind`, `run_id`, `site_profile_id`, `site_profile_digest`, `compute_profile_digest`, `source_commit`, `code_identity`, `runtime_lock_path`, `runtime_lock_digest`, `image_id`, `audit_log`, `audit_tail_digest`, `job_id`, `fetched_artifacts`, `settlement_digest`, `signature`。

#### [MODIFY] [compute_profile_qualification.py](file:///Users/xjchen/bench/mlffbench/dftworld_bench/experiments/compute_profile_qualification.py)
* **严格 Trusted Root**：所有相关文件（audit log, runtime lock, artifacts 等）必须使用相对路径且解析后严格限制在 `evidence/` 根目录，拒绝软链接（symlink）和绝对路径。
* **Runtime Lock 真实核验**：Verifier 读取 Lock 文件，校验 `receipt.image_id == lock.artifact.image_id == audit.image_id == job.image_id`。
* **Audit Lineage 成为必需**：收据中 `audit_log` 字段必填。验证所有生命周期事件（`INSTANCE_CREATE_INTENT` 至 `SETTLEMENT_COMPLETE`）连续且参数一致。
* **Ed25519 签名验证**：
  * 使用 `cryptography.hazmat.primitives.asymmetric.ed25519`。
  * 提取 `SiteProfile` 中的公钥，对 canonical receipt + audit tail digest + runtime lock digest 进行签名验证。

#### [MODIFY] [audit.py](file:///Users/xjchen/bench/mlffbench/dftworld_bench/hpc/audit.py)
* `GatewayAudit.append()` 增加文件锁（`flock`），在锁内重新加载尾部 entry、断言序列号连续，执行 append、flush 及 `os.fsync()`，保证并发与崩溃一致性。

---

### P5：Ownership Marker 与 Zero-Orphan 语义收紧 (Commit 4)

#### [MODIFY] [instance_manager.py](file:///Users/xjchen/bench/mlffbench/dftworld_bench/hpc/drivers/compshare/instance_manager.py) 与 [cli.py](file:///Users/xjchen/bench/mlffbench/dftworld_bench/hpc/drivers/compshare/cli.py)
* **稳定 Hash Owner Token**：
  ```python
  owner_token = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
  name = f"mlffbench-{owner_token}"
  remark = f"mlffbench:run:{owner_token}"
  ```
  彻底规避特殊字符与云平台长度限制。
* **收紧实例终态**：
  * 仅 `DELETED` 和 `TERMINATED` 视为安全。
  * `STOPPED`, `STARTING`, `STOPPING`, `RUNNING`, `UNKNOWN`, `FAILED` 一律视为未清理（依然计费或占用资源）。
* **云查询 Fail-Closed**：鉴权失败、超时、分页未完、返回异常格式一律 Fail-Closed。

---

### P6：SiteProfile 驱动 Qualification 策略 (Commit 4)

#### [MODIFY] [hpc-site-profile.schema.json](file:///Users/xjchen/bench/mlffbench/schemas/hpc-site-profile.schema.json)
* 扩充 `qualification_policy` 对象：
  ```json
  "qualification_policy": {
    "type": "object",
    "properties": {
      "required_probe_classes": { "type": "array", "items": { "type": "string" } },
      "required_runtime_capabilities": { "type": "array", "items": { "type": "string" } }
    },
    "required": ["required_probe_classes"]
  }
  ```

#### [MODIFY] [compute_profile_qualification.py](file:///Users/xjchen/bench/mlffbench/dftworld_bench/experiments/compute_profile_qualification.py)
* **消除 Scheduler 猜测**：严格按照 `route → site_profile_id → SiteProfile.scheduler` 加载，绝不按 `cpu` / `gpu` 字符串硬编码猜测。
* **SiteProfile 决定 Canary 门槛**：不再根据收据中出现了什么 Canary 反推需要什么，严格按 `SiteProfile.qualification_policy.required_probe_classes` 判定（例如 CPU site 要求 `["cpu"]`，CompShare 要求 `["gpu"]`）。

---

### P7：负向安全门禁与全仓防范 (Commit 5)

#### [NEW] [test_gate_a1_negative_contracts.py](file:///Users/xjchen/bench/mlffbench/tests/hpc/test_gate_a1_negative_contracts.py)
集中构建专门的“**无法通过（Must Fail）**”负向防御测试套件：
1. `test_unqualified_runtime_cannot_resolve`: 未通过 qualification 的 runtime 无法被 resolve；
2. `test_unqualified_runtime_cannot_advertise`: 未通过 qualification 的 runtime 不出现在 capabilities 中；
3. `test_unqualified_runtime_cannot_submit`: 未通过 qualification 的 runtime 提交时被 Driver 拦截；
4. `test_self_authored_receipt_cannot_pass`: 伪造/手写收据（无 audit、无签名或 hash 篡改）无法通过验证；
5. `test_revoked_token_cannot_skip_teardown`: 显式 revoke token 后 close 依然执行资源 teardown；
6. `test_recovery_failure_blocks_production_ready`: 实例恢复失败时阻止系统进入 READY；
7. `test_stopped_instance_cannot_pass_zero_orphan`: `STOPPED` 状态的实例无法通过 Zero-Orphan 门禁；
8. `test_receipt_cannot_choose_its_own_probes`: 收据缺少 SiteProfile 规定的 Probe 时自动判定 FAIL。

#### [MODIFY] [test_release_gates_g0_g15.py](file:///Users/xjchen/bench/mlffbench/tests/hpc/test_release_gates_g0_g15.py)
* 在 G15 发布门禁中硬性挂载 Gate A1 负向防御测试套件，检查各项安全指标。

---

### P8：文档规范与 5 个独立提交发布 (Commit 5)

1. 更新 [SKILL.md](file:///Users/xjchen/bench/mlffbench/base-env-build/skills/hpc-submit/SKILL.md)：明确说明 Agent 仅可选择 `compute_class` 与 runtime capability，不可指定 ImageId、Scheduler、实例名或凭据。
2. 更新 [compshare_gpu_images.md](file:///Users/xjchen/bench/mlffbench/docs/maintainer/compshare_gpu_images.md) 与 [slurm_quickstart.md](file:///Users/xjchen/bench/mlffbench/docs/user/slurm_quickstart.md)。
3. 更新 `implementation_plan.md` 与 `walkthrough.md`，忠实记录 Gate A1 现状与加固结果。
4. **按 5 个原子 Commit 提交**：
   * Commit 1: `feat(hpc): runtime availability, lock v2, and capability filtering (P1)`
   * Commit 2: `fix(hpc): token-independent trusted teardown and production recovery (P2, P3)`
   * Commit 3: `feat(hpc): receipt schema, GatewayAudit lineage, and Ed25519 verification (P4)`
   * Commit 4: `fix(hpc): ownership marker, safe states, and SiteProfile-driven qualification (P5, P6)`
   * Commit 5: `test(hpc): Gate A1 negative contracts suite, docs, and freeze verification (P7, P8)`

---

## 验证计划 (Verification Plan)

### 自动化测试执行序列
1. 单元测试与契约测试：
   ```bash
   /Users/xjchen/bench/mlffbench/.venv/bin/pytest tests/hpc/test_gate_a1_negative_contracts.py
   /Users/xjchen/bench/mlffbench/.venv/bin/pytest tests/hpc/test_runtime_resolution.py
   /Users/xjchen/bench/mlffbench/.venv/bin/pytest tests/hpc/test_compshare_driver.py
   /Users/xjchen/bench/mlffbench/.venv/bin/pytest tests/hpc/test_compute_profile_qualification.py
   ```
2. 全仓回归测试（预期 > 1350 passed, 2 skipped）：
   ```bash
   /Users/xjchen/bench/mlffbench/.venv/bin/pytest
   ```
3. 代码格式与 Git 排查：
   ```bash
   git diff --check
   ```
4. 运行探针核验：
   ```bash
   python -c "from dftworld_bench.hpc.runtime_resolution import default_resolver; r = default_resolver(); print(r.qualified_capabilities())"
   # 输出必须为 []
   ```
