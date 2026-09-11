# TASK-S5-01 验收报告 —— 记忆四层 + 租户隔离 + 遗忘

> 任务书：[TASK-S5-01_记忆四层与租户隔离.md](TASK-S5-01_记忆四层与租户隔离.md)
> 分层映射：[TASK-S5-01_记忆分层映射表.md](TASK-S5-01_记忆分层映射表.md)
> 设计来源：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §3.11 / §4.3 / P7.2-08 / P7.2-10 / §8 / §10
> worktree：`.worktrees/s501`（`--base master`，基线 `dced7c4f`）｜日期：2026-09-12

---

## 一、交付物清单

| # | 交付物 | 路径 | 规模 |
|---|---|---|---|
| 1 | 四层模型（§3.11 对齐） | `agent/memory/taxonomy.py` | 616 行 |
| 2 | 租户隔离矩阵与写入守卫（P7.2-08） | `agent/memory/tenancy.py` | 808 行 |
| 3 | 四层分片存储（复用既有引擎与检索） | `agent/memory/layered_store.py` | 817 行 |
| 4 | 遗忘三触发 + TTL + 快照 30 天 + 被遗忘权 | `agent/memory/forgetting.py` | 1329 行 |
| 5 | 主体伪名化与审计桥（匿名化 = 销毁盐） | `agent/memory/identity.py` | 318 行 |
| 6 | 包导出接线（兼容叠加） | `agent/memory/__init__.py` | +84 行 |
| 7 | 单测（4 套件 + 1 公共夹具模块） | `tests/unit/test_memory_{taxonomy,tenancy,layered_store,forgetting}.py` + `tests/unit/memory_layer_testkit.py` | **246 例 / 2320 行** |
| 8 | 记忆分层映射表 | `docs/zh/CloudPivot_v7.2重构计划/TASK-S5-01_记忆分层映射表.md` | — |
| 9 | 本验收报告 | 同目录 | — |
| 10 | 交付结案报告 | `S5-01_交付结案报告_20260912.md` | — |

**代码总规模**：3888 行（5 个新模块）+ 246 例单测。**零运行时目录污染**（见 §四.4）。

---

## 二、任务书 §四 验收清单逐条核验

### ✅ 1. 记忆条目模型对齐 §3.11（type/tenant/subject/scope/ttl/forget_candidate）

`agent/memory/taxonomy.py::MemoryEntry` 逐字落地 §3.11 十四字段：

```
id / tenant_id / subject_id / type / content_redacted / content_hash / scope /
source_task_id / confidence / created_at / last_hit_at / ttl_expires_at /
forget_candidate / schema_version
```

- `type ∈ {working, fact, preference, strategy}`（四层，§4.3）
- `scope ∈ {project:<workspace-hash>, global}`
- `content_hash` **由脱敏后文本派生**（§3.4：脱敏先于哈希），空值自动补齐
- 扩展字段（`source_capability_id` / `org_level` / `degraded` / `forget_reason` / `hit_count` / `extra`）全部带默认值 ⇒ 按 §3.11 最小集构造亦合法
- `tenancy.ISOLATION_MATRIX` 四行 = `working/fact/preference/strategy`，`isolation_matrix_rows()` 输出机器可读矩阵

证据：
```powershell
python -m pytest tests/unit/test_memory_taxonomy.py -q -p no:randomly
# 50 passed
python -m pytest tests/unit/test_memory_taxonomy.py::TestMemoryEntryModel -q -p no:randomly
# 含 test_section_3_11_fields_all_present / test_minimal_section_3_11_construction_is_valid
#    test_content_hash_derived_from_redacted_content / test_content_hash_is_not_of_raw_content
```

### ✅ 2. 租户隔离矩阵单测通过：同租户事实/策略可见、跨租户不可见

`tests/unit/test_memory_tenancy.py::TestVisibilityMatrix` + `tests/unit/test_memory_layered_store.py::TestTenantIsolation`（判定函数层 + 真实分片落盘层双覆盖）。

证据：
```powershell
python -m pytest tests/unit/test_memory_tenancy.py tests/unit/test_memory_layered_store.py `
  -k "cross_tenant or carried or pollute or strategy_layer or read_only or invisible or leak" -v -p no:randomly
# 24 passed, 162 deselected
```

### ✅ 3. 偏好记忆跨租户跟随 subject（携带验证）；不污染策略层（写入被拒用例）

**反例证据（三条铁律的取证）**：

| 铁律 | 用例 | 断言 |
|---|---|---|
| 跨租户**不可见**（事实/工作/租户内策略） | `TestTenantIsolation::test_cross_tenant_fact_is_invisible` | A 写事实 → B 召回 `== []`；A 召回 `!= []`（同租户可见对照） |
| 跨租户**不可见**（判定函数层） | `TestVisibilityMatrix::test_cross_tenant_fact_is_invisible` | `policy.is_visible(entry_tenantA, ctx_tenantB) is False` |
| 跨租户**不可见**（按 id 取） | `TestTenantIsolation::test_cross_tenant_get_returns_none` | `get(id, ctx=B) is None` / `get(id, ctx=A) is not None` |
| 偏好**可携带** | `TestPreferenceCarry::test_preference_is_carried_to_another_tenant` | A 写偏好（subject=alice）→ B（subject=alice）召回命中 |
| 偏好**不可泄漏给其他主体**（反例） | `TestPreferenceCarry::test_preference_is_invisible_to_another_subject` | A/B 租户下 subject=bob 均召回 `== []` |
| 策略层**不可污染**（反例） | `TestStrategyLayerProtection::test_personal_write_to_strategy_layer_is_rejected` | `pytest.raises(MemoryWriteRejected)`，reason `strategy_layer_readonly`，且 `all_entries() == []`（**零落盘**） |
| 偏好**不得升格为 org 级** | `TestStrategyLayerProtection::test_preference_cannot_be_org_level` | reason `preference_cannot_be_org_level` |

### ✅ 4. 企业策略记忆只读下发（启用时个人不可写）

- org 级下发：`store.write(..., memory_type="strategy", channel=WriteChannel.ORG)` → `tenant_id="__org__"`、`org_level=True`、落 `org/strategy.db`
- 只读可见：`TestStrategyLayerProtection::test_org_strategy_is_delivered_read_only_to_other_tenant` —— **租户 B 能读到 A 下发的企业策略**
- 个人不可写：`test_org_strategy_is_readonly_for_personal_channel`（`update_entry(channel=USER)` 抛 `MemoryWriteRejected`）
- 治理通道可维护：`test_org_strategy_writable_through_governance_channels`（ORG/SYSTEM 可改，否则组织无法更新自己下发的策略、也无法执行遗忘）
- 个人写策略层：`decide_write("strategy", ctx, channel=USER)` → `REJECT/strategy_layer_readonly`（**即使缺租户字段也先拒绝**，确保铁律优先于降级出口）

### ✅ 5. 遗忘三触发各自有触发用例；快照留 30 天；删除后审计匿名化但链保留

`tests/unit/test_memory_forgetting.py`（66 例，四组各自独立）：

| 触发 | 用例 | 关键断言 |
|---|---|---|
| ① 成功率 30 天 < 基线×0.7 | `TestSuccessRateTrigger::test_below_baseline_ratio_triggers` | 观测 0.40 < 0.9×0.7=0.63 → 候选，evidence 含 `observed_rate/baseline/threshold/samples/window_days` |
| ① 边界与门槛 | `test_exactly_at_threshold_does_not_trigger` / `test_insufficient_samples_does_not_trigger`（样本 < 20 不判劣化）/ `test_no_quality_baseline_does_not_trigger` | 三条反例 |
| ① **租户作用域**（修复 #9） | `TestSuccessRateTenantScoping`（7 例） | 租户 A 全失败 + 租户 B 全成功 ⇒ **B 的记忆不得被判劣化**；同组轨迹下 A 的条目标正常触发；无法归属租户的轨迹被排除并计数（`unattributed`）；org 级策略用 org 聚合样本 |
| ② 来源失效 | `test_deprecated_source_triggers` / `test_removed_source_triggers` | reason `source_deprecated` / `source_removed` |
| ② 反例 | `test_healthy_source_does_not_trigger` / `test_alias_merge_is_not_treated_as_removal` / `test_broken_registry_does_not_mark_invalid` | 改名≠摘除；数据源不可用不误删 |
| ③ 删除权 | `test_erasure_deletes_subject_entries_across_tenants` | 跨租户删除该主体记忆，他人记忆不受影响 |
| 补充 TTL | `TestTTLTrigger`（6 例） | 到期 → `forget_candidate` + `forget_reason=ttl_expired`，且**持久化**（重开实例仍可见） |

**先快照后删除**：`TestForgetSnapshotFirst::test_snapshot_is_written_before_deletion`（快照文件存在、`count==1`、含内容）→ `test_deletion_is_physical`（重开实例 `get() is None`）。

**快照留 30 天**：`TestSnapshotStore::test_default_retention_is_30_days`、`test_prune_keeps_until_expiry_then_removes`（+29d 保留 / +31d 删除）、`test_verify_detects_tampering`（摘要验签）。

**删除后审计匿名化但链保留**（核心验收，`TestRightToBeForgotten`）：

```powershell
python -m pytest tests/unit/test_memory_forgetting.py::TestRightToBeForgotten -v -p no:randomly
```

`test_erasure_records_audit_and_keeps_chain_verifiable` 断言链：

| 断言 | 含义 |
|---|---|
| `deleted_count == 1` | 记忆**物理删除**（重开实例也取不到） |
| `audit_recorded is True` | 追加 `memory.erase` 链记录（证据留存） |
| `chain_verified is True` + `chain_checked > 0` | **审计链未因删除而断裂，验签通过** |
| `residual_identifier_hits == []` | 链内**无原始 subject_id 残留**（匿名化达成） |
| `anonymized is True` | 盐已销毁（crypto-shredding）+ 无原始标识符残留 |
| `pseudonym_before` 形如 `anon-*` → `pseudonym_after == "anon-erased"` | 伪名不可再关联、且**不再签发新盐**（匿名化不可逆） |
| `chain_pseudonym_present is True` | 旧伪名仍在链上（**删的是记忆不是证据**） |

配套：
- `test_audit_payload_never_carries_content_or_raw_subject`：审计载荷不含内容与原始标识符（`MemoryEntry.to_audit_leaf()` 只落 `content_hash` 等叶字段）
- `test_erasure_uses_hash_only_snapshot`：删除权用 `hash_only` **墓碑快照**（内容置空 + 主体换伪引用 + 保留 `content_hash`）
- `test_erasure_redacts_prior_full_snapshots`：删除权**穿透既有 full 快照**（①/② 类遗留的 30 天可回滚快照中的该主体条目就地墓碑化），否则"物理删除"被快照抵消

### ✅ 6. TTL 到期自动降级候选生效

- `taxonomy.ttl_seconds_for()`：工作 8h / 事实 180d / 偏好 365d / 策略 365d（`MEMORY_TTL_*` 可配，非法值回退默认）
- `LayeredMemoryStore` 注入时钟 ⇒ TTL 断言不依赖真实时钟（START 坑 3）：`TestTTL::test_expired_entry_is_not_recalled_by_default`
- `ForgettingEngine.apply_ttl()` 到期 → 标记候选并持久化：`TestTTLTrigger::test_ttl_marking_is_persisted`
- 召回闸门：过期条目默认不召回，`include_expired=True` 可取：`test_expired_entry_still_recallable_when_requested`

### ✅ 7. 既有 memory/knowledge 套件零回归；新增单测全绿、覆盖率 ≥80%

- 新增单测 **236 例全绿**，新增模块覆盖率 **91%**（见 §四.1）
- 相关套件 + 邻接回归 **零回归**（见 §四.2）

---

## 三、隔离矩阵用例结果（正反例总表）

| # | 层 | 场景 | 期望 | 用例 | 结果 |
|---|---|---|---|---|---|
| 1 | fact | 同租户召回 | 可见 | `TestTenantIsolation::test_same_tenant_fact_is_recalled` | ✅ |
| 2 | fact | **跨租户召回** | **不可见** | `TestTenantIsolation::test_cross_tenant_fact_is_invisible` | ✅ |
| 3 | fact | 跨租户按 id 取 | `None` | `test_cross_tenant_get_returns_none` | ✅ |
| 4 | fact | 跨租户判定函数 | `False` | `TestVisibilityMatrix::test_cross_tenant_fact_is_invisible` | ✅ |
| 5 | fact | 无租户上下文召回 | 不可见 | `test_context_without_tenant_sees_nothing_tenant_scoped` | ✅ |
| 6 | fact | scope 哈希与上下文 workspace 不一致 | 不可见 | `test_project_scope_hash_must_match_context_workspace` | ✅ |
| 7 | working | 跨租户召回 | 不可见 | `test_cross_tenant_working_memory_is_invisible`（判定 + 存储双覆盖） | ✅ |
| 8 | strategy（租户内） | 跨租户召回 | 不可见 | `test_cross_tenant_tenant_local_strategy_is_invisible`（双覆盖） | ✅ |
| 9 | strategy（org） | 跨租户读取 | **可见（只读）** | `test_org_strategy_is_delivered_read_only_to_other_tenant` | ✅ |
| 10 | strategy（org） | 个人通道修改 | 抛 `MemoryWriteRejected` | `test_org_strategy_is_readonly_for_personal_channel` | ✅ |
| 11 | strategy | 个人通道写入 | 拒绝 + 零落盘 | `test_personal_write_to_strategy_layer_is_rejected` | ✅ |
| 12 | preference | **换租户同主体召回** | **可见（携带）** | `TestPreferenceCarry::test_preference_is_carried_to_another_tenant` | ✅ |
| 13 | preference | **同/跨租户换主体召回** | **不可见** | `test_preference_is_invisible_to_another_subject` | ✅ |
| 14 | preference | 上下文无 subject | 不可见 | `test_preference_invisible_without_subject_context` | ✅ |
| 15 | preference | 写入缺 subject | 拒绝 | `test_preference_without_subject_is_rejected` | ✅ |
| 16 | preference | 标记 org 级 | 拒绝 | `test_preference_cannot_be_org_level` | ✅ |
| 17 | 任意（租户隔离层） | 写入缺 tenant | 拒绝（默认） | `TestEnforcedTenancy::test_missing_tenant_is_rejected` | ✅ |
| 18 | 任意（租户隔离层） | 写入缺 workspace（未显式给 scope） | 拒绝（**不静默降 global**） | `test_missing_workspace_is_rejected_for_project_layers` | ✅ |
| 19 | 任意 | 显式 `scope=global` 的租户级事实 | 接受 | `test_explicit_global_scope_is_allowed_for_tenant_wide_fact` | ✅ |
| 20 | 任意 | 显式 scope 跨工作区 | 拒绝 | `test_scope_workspace_mismatch_is_rejected` | ✅ |
| 21 | 任意 | 显式降级（`allow_missing_tenancy`） | 落 `__unscoped__` + 默认召回不含 | `TestEnforcedTenancy::test_degrade_mode_quarantines_entry` | ✅ |
| 22 | 任意 | 召回优先级 | 策略 > 事实 > 偏好 > 工作；project 事实 > global 偏好；同级新者胜 | `TestRecallPriority`（5 例）+ `TestRecallPriority`（taxonomy 7 例） | ✅ |
| 23 | 任意 | 只读操作零落盘 | 不创建任何文件 | `TestRecallPriority::test_read_only_operations_leave_no_files` | ✅ |
| 24 | 任意 | **治理决策不跨租户串扰**（修复 #9） | 租户 A 的劣化不触发租户 B 的记忆遗忘 | `TestSuccessRateTenantScoping::test_other_tenant_failures_do_not_trigger_this_tenant`（＋正例 `test_own_tenant_failures_do_trigger`） | ✅ |
| 25 | 任意 | 不可归属租户的轨迹不参与判定 | 被排除并计数 | `test_adapter_scopes_sample_and_reports_unattributed` | ✅ |
| 26 | 任意 | org 级策略用 org 聚合样本 | `sample_scope == ""` | `test_org_level_strategy_uses_org_wide_sample` | ✅ |
| 27 | 任意 | 删除权取证扫**全链** | `chain_scanned == 链长`（非尾部截断） | `test_chain_scan_covers_whole_chain_not_a_tail`（修复 #10） | ✅ |

统计：**24 例隔离矩阵用例全绿**（判定/存储层 `-k` 选择结果：`24 passed, 162 deselected`），连同新增的治理决策隔离 4 例，本任务隔离相关用例共 **28 例**。

---

## 四、质量证据

### 4.1 新增单测与覆盖率

```powershell
python -m coverage run --source=agent.memory.taxonomy,agent.memory.tenancy,agent.memory.layered_store,agent.memory.forgetting,agent.memory.identity `
  -m pytest tests/unit/test_memory_taxonomy.py tests/unit/test_memory_tenancy.py `
           tests/unit/test_memory_layered_store.py tests/unit/test_memory_forgetting.py -q -p no:randomly
python -m coverage report -m
```

| 模块 | 语句数 | 覆盖率 |
|---|---|---|
| `agent/memory/taxonomy.py` | 231 | **95%** |
| `agent/memory/tenancy.py` | 258 | **97%** |
| `agent/memory/layered_store.py` | 320 | **88%** |
| `agent/memory/forgetting.py` | 605 | **91%** |
| `agent/memory/identity.py` | 141 | **83%** |
| **合计** | **1555** | **91%** |

| 套件 | 用例数 |
|---|---|
| `test_memory_taxonomy.py` | 50 |
| `test_memory_tenancy.py` | 61 |
| `test_memory_layered_store.py` | 59 |
| `test_memory_forgetting.py` | 76 |
| **合计** | **246** |

### 4.2 相关套件与邻接回归

```powershell
# 相关套件 + 邻接 + 安全回归（49 文件：test_memory* / test_knowledge* /
#   test_memory_abstractor* / skills_mgmt / trace_v2 / 链式审计 / tests/regression/test_p0_security_fix.py）
python -m pytest <49 files> -q -p no:randomly
# 1678 passed, 59 skipped, 1 xfailed, 4 xpassed, 0 failed（105.03s）

# 分开口径（首轮交付时）
# 相关套件 42 文件：1198 passed / 0 failed
# 邻接 7 文件（skills_mgmt + trace_v2 + 链式审计）：420 passed / 0 failed / 1 xfailed
```

> `tests/regression/test_p0_security_fix.py`（含 `TestCrossModuleConsistency`）**全绿** ——
> 本任务只**读取**既有脱敏设施（`SensitiveDataFilter.detect_and_sanitize`），
> 未改动 `sensitive_data_filter.py` / `error_reporting_config.py`，安全回归不受影响。

全量抽查（`tests/unit -m "not slow"`）：

```powershell
python -m pytest tests/unit -m "not slow" -p no:randomly -q --no-header
# 10 failed, 14050 passed, 51 skipped, 255 deselected, 13 xfailed, 4 xpassed（2035.90s / 33:55）
```

**10 例失败已核实为既有失败（与本任务无关，非回归）**：

| 失败用例 | 数量 |
|---|---|
| `tests/unit/test_ci_l3_context_preflight.py::TestSimulatedCiFailure::test_ci_command_contract_exit_code` | 1 |
| `tests/unit/test_preflight_runner.py::test_cli_exit_zero_on_success` / `test_cli_fake_fail_env_exit_one` / `test_cli_fake_fail_env_empty_means_normal` | 3 |
| `tests/unit/test_mcp_executor.py::TestVerboseCliFlag::*` | 6 |

归因证据（**在未改动的 master 基线工作区 `dced7c4f` 上复现同样的 10 例**）：

```powershell
cd C:\Users\Administrator\agent            # 主工作区，未含任何 S5-01 改动
python -m pytest tests/unit/test_ci_l3_context_preflight.py::TestSimulatedCiFailure::test_ci_command_contract_exit_code `
  tests/unit/test_preflight_runner.py tests/unit/test_mcp_executor.py::TestVerboseCliFlag -q -p no:randomly --tb=no
# 10 failed, 10 passed —— 与 worktree 内结果逐例一致
```

根因：这 10 例均为**子进程 CLI 契约测试**（`python -m ... --verbose` 等），需要捕获子进程 stdio；
本会话沙箱禁止通过**命名管道**捕获子进程输出，子进程 `stdout/stderr` 返回 `None`，
断言处报 `TypeError: unsupported operand type(s) for +: 'NoneType' and 'NoneType'`
（`test_mcp_executor.py:761/771/781/800/823/852`）。属**环境限制**（CI Linux 无此约束），
与记忆四层改动无依赖路径交叉。

> 结论：`14050 passed / 10 failed` 中，**10 例失败在基线上逐例同现 ⇒ S5-01 引入 0 回归**。

### 4.3 门禁

| 门禁 | 命令 | 结果 |
|---|---|---|
| kwarg 扫描（agent） | `python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH` | HIGH **0** / 合计 **0** |
| kwarg 扫描（tests） | `python scripts/scan_kwarg_conflicts.py --path tests --min-risk HIGH` | HIGH **0** / 合计 **0** |
| mypy（新增模块） | `python -m mypy agent/memory/{taxonomy,tenancy,layered_store,forgetting,identity,__init__}.py` | **新增模块 0 error**（`agent\memory\*` 无任何诊断行） |
| mypy（全图对照） | 同上（mypy 跟import 展开 86 文件） | 493 errors / 86 files —— 全部为**既有**错误，位于 `agent/extensions/`、`memory/`、`agent/descriptors/` 等；修复前该口径为 496/87（差额 3 = 本次修复的 3 处新增诊断，已清零） |
| mypy（既有阻塞模块） | `python -m mypy agent/env_config_manager.py` / `agent/network_config.py` | 477 errors / 80 files（两份一致）—— 与本任务无依赖路径交叉，**无增量** |
| importlinter | `lint-imports --config .importlinter` | **2 kept, 0 broken**（473 files / 1298 deps） |
| pre-commit（真实钩子） | `pre-commit run --files <11 个新增/改动文件>` | 9 钩子：**8 Passed / 1 Skipped**（`ps-script-analyzer` 无 PS 文件），0 Failed |

> 环境提示：`lint-imports` 在本机默认 GBK 控制台下读取 UTF-8 的 `.importlinter` 会抛
> `'gbk' codec can't decode byte 0x90`；置 `PYTHONUTF8=1` 后正常（2 kept / 0 broken）。
> 这是控制台编码问题，不是契约问题，已在交付结案报告中记为环境遗留。

### 4.4 运行时目录零污染

- 四个套件的 autouse 夹具（`memory_layer_testkit.memory_runtime`）把 `MEMORY_LAYERS_ROOT` /
  `MEMORY_SNAPSHOT_ROOT` / `MEMORY_IDENTITY_ROOT` 全部重定向到 `tmp_path`，并**默认关闭进程级审计门面**
  （需要取证链的用例改绑 `bound_audit_chain` 的 tmp 台账）；
- 默认落点本身也在**项目树之外**（`~/.cloudpivot/vault/...`，§8 P7.2-18），即使有人用默认构造也不会污染仓库；
- 专项断言：`test_read_only_operations_leave_no_files`（只读召回不创建任何分片）、
  `test_rejected_write_creates_no_shard`（拒绝路径零落盘）、`test_default_root_is_outside_project_tree`；
- 跑完门禁后 `git status`：仅 11 个预期新增/改动文件，`.coverage` / `.pytest_tmp` 均被 `.gitignore` 覆盖，**无产物漂移**。

---

## 五、实现期发现并修复的问题（自测反例驱动）

| # | 问题 | 发现方式 | 处置 |
|---|---|---|---|
| 1 | 审计载荷把 **原始 `subject_id` 与记忆内容**一并入链 ⇒ 被遗忘权无法达成（链 append-only，事后不可改写） | `erase_subject` 的残留扫描断言 | 新增 `MemoryEntry.to_audit_leaf()`（只落 `content_hash` 等叶字段，不含内容与主体原文），存储层审计改走该通道；补 `test_audit_payload_never_carries_content_or_raw_subject` |
| 2 | 擦除后再调 `pseudonym()` 会**签发新盐**，把"不可逆匿名化"变回可逆 | 语义自查 + 用例 | `salt_for()` 对已擦除主体不再签发（sticky shredded）；补 `test_pseudonym_shredding_is_irreversible` |
| 3 | ①/② 类遗留的 `full` 快照（留 30 天）会**抵消删除权的物理删除** | 设计自审 | 新增 `MemorySnapshotStore.redact_subject()`：删除权执行时就地墓碑化既有快照中的该主体条目；补 `test_erasure_redacts_prior_full_snapshots` |
| 4 | 写入缺 workspace 时被**静默降为 `global` 作用域**（等于悄悄放宽隔离面） | 用例 `test_missing_workspace_is_rejected_for_project_scope` 首轮失败 | 未显式给 `scope` 时，project 类层缺 workspace ⇒ 拒绝（`missing_workspace_id`） |
| 5 | `is_readonly` 把 **org 治理通道**也判为只读 ⇒ 组织无法更新自己下发的策略、遗忘引擎无法标记 org 级条目 | 用例首轮失败 | 只读语义收敛为"**个人通道**只读"（P7.2-08 原文约束的就是个人写入） |
| 6 | `run(execute=False)` 仍会清理到期快照（dry-run 下发生了删除动作） | 用例首轮失败 | dry-run 一律不删任何东西（快照清理亦属删除） |
| 7 | 空 `capability_id` 被判为"来源失效" ⇒ 无来源指针的记忆会被误清理 | 用例首轮失败 | `SourceStatus.invalid` 对空指针返回 `False`（无可判定对象 ⇒ 不判失效） |
| 8 | `LayeredMemoryStore.stats()` 复用既有 `get_stats()` 的键名口径 | 实现期 | 直接消费既有 `total_entries`，不新增统计口径 |
| 9 | **触发①按跨租户聚合样本判定** ⇒ 租户 A 的劣化会触发租户 B 的记忆被遗忘（破坏性决策跨租户串扰，违反 P7.2-08） | 交付后复读 S2-01/S2-02 台账能力时发现（`UnifiedTraceStore.query()` 无租户参数） | `tenancy.sample_scope_for()` + `TraceQualitySource` **读取侧**按 trace `tenancy` 过滤；不可归属轨迹排除并计数；org 级策略用 org 聚合；补 `TestSuccessRateTenantScoping`（7 例）。**守不易**：未改 `trace_v2.py` |
| 10 | 删除权的"链内无原始标识符残留"只扫**最近 5000 条** ⇒ 结论不可量化、长链下不成立 | 同轮复读（`facade.recent(limit=5000)`） | 改为**分批全链扫描**（`chain.iter_entries(batch=500)`），扫描条数如实记入 `ErasureResult.chain_scanned`；`MEMORY_AUDIT_SCAN_LIMIT` 可设上限（默认 0 = 全链）；补 2 例 |
| 11 | `evaluate_success_rate` 的 `sample=None` 分支残留改名后的 `NameError`（`scan()` 总预取样本，故该分支未被任何用例覆盖） | **mypy**（`Name "scope_tenant_for" is not defined`） | 修正为 `sample_scope_for`；补 `test_evaluate_success_rate_without_precomputed_sample` 覆盖该分支并断言确实查了数据源。**教训**：未被用例触达的分支靠类型门禁兜底 |

---

## 六、遗留问题（逐条带归属与阻塞性判定）

| # | 遗留 | 归属 | 阻塞性 |
|---|---|---|---|
| L1 | **P7.2-10 记忆→组装注入未接线**：`ContextAssembler` 的 system 区注入优先级（策略 > 事实 > 偏好）尚未消费 `LayeredMemoryStore.recall()` | S6-01 / 后续（本任务边界声明 §七.4） | 不阻塞：本任务已把优先级固化为可测的 `recall_priority_key()`，接口就绪 |
| L2 | **"做梦"聚合未实现**：§4.3 的"低频 + 只读快照 + 产出 PR" | S5-02 / 后续（任务书 §一.3 明确本任务只落遗忘与隔离） | 不阻塞 |
| L3 | **`memory_abstractor` 未接入分层存储**：其 `_load_long_term_memories()` 仍读默认路径 `LongTermMemory()`（无注入缝） | 下游（技能萃取线） | 不阻塞：邻接回归零回归；接入需先给该方法加 `db_path` 注入缝 |
| L4 | **`knowledge` 卡片无 tenancy**：`agent/knowledge/` 零租户字段，`Card.scope` 是适用边界而非租户 | 后续（跨租户知识面） | 不阻塞：已在分层映射表 §七.2 边界声明 |
| L5 | **多租户企业形态（tenant ≠ workspace）** 仅在 `resolve_tenancy` 层支持（显式 `tenant_id` + `workspace_id`），未有单测覆盖"一个租户多工作区"的完整召回面 | 后续（企业侧启用时） | 不阻塞：IDE 先行口径（P7.2-08）下 tenant = workspace-hash，已覆盖 |
| L6 | 触发①的**基线来源**目前取 `descriptor.quality.success_rate`；S5-02 的 L2 Core-50 基线就绪后可切换为回归基线指针（`quality.regression_baseline_id`） | S5-02 交付后 | 不阻塞：`baseline_provider` 已是可注入缝 |
| L7 | `lint-imports` 在本机 GBK 控制台需 `PYTHONUTF8=1` 才能读 UTF-8 的 `.importlinter` | 环境/工具链 | 不阻塞：属控制台编码，CI Linux 不受影响 |
| L8 | **审计台账保留/归档策略缺失**（链式轨只增不减、无 TTL）—— S2-02 遗留 #4 原定归属" S2 生产化 / **S5**" | S5 轨后续（非 S5-01 验收项） | 不阻塞本任务：S5-01 的遗忘只删**记忆**，明确不删审计证据（§8「删的是记忆不是证据」）；台账保留策略需独立任务（涉及 `AuditChain` 的归档/只读冷存） |
| L9 | 触发①基线除 `descriptor.quality.success_rate` 外，仓库已有现成替代数据源 `agent/digestion/gate.py::baseline_from_ledger(capability_id, *, store=None, limit=500)` / `baseline_from_traces(rows)` | S5-02 交付后统一 | 不阻塞：`baseline_provider` 已是可注入缝，切换为一行接线 |
| L10 | 触发①的租户作用域过滤在**读取侧**完成（`query()` 无租户参数 ⇒ 需拉取该能力全部轨迹再过滤） | S2 生产化（给 `UnifiedTraceStore.query` 加 `tenant_id`/`workspace_id` 过滤 + 用既有 `idx_ut_workspace` 索引） | 不阻塞：正确性已保证；轨迹量大时是该路径的已知成本，已在映射表 §五记录 |
| L11 | `AuditChain.entries()` 无 `workspace_id` 过滤（列已存在但未建索引） | S2 生产化 / S4-01 | 不阻塞本任务：删除权取证扫描是**治理通道**（跨租户全链扫描是必需的，否则无法证明"标识符已全局消失"） |

---

## 七、结论

任务书 §四 验收清单 **7/7 全部通过**：

1. §3.11 条目模型对齐（14 字段 + 四层枚举 + 作用域形态）✅
2. 租户隔离矩阵单测通过（同租户可见 / **跨租户不可见**，判定层 + 存储层双覆盖）✅
3. 偏好跨租户跟随 subject（携带验证）+ 不污染策略层（写入被拒 + 零落盘）✅
4. 企业策略记忆 org 级只读下发（个人不可写、跨租户可读、治理通道可维护）✅
5. 遗忘三触发各自触发用例 + 先快照后删除 + 快照留 30 天 + **删除后审计匿名化但链保留可验签** ✅
6. TTL 到期自动降级候选生效（含短/长 TTL 分层与可配回退）✅
7. 既有 memory/knowledge 套件零回归；新增 **246** 例全绿、覆盖率 **91%**（≥80%）✅

**补充验证（复用 S2-01/S2-02 台账能力复读后加固）**：
- 触发①的成功率样本**按条目租户作用域**（修复 #9）——治理决策不跨租户串扰；
- 删除权取证**扫全链**并报出扫描条数（修复 #10）；
- `mypy` 拦下未被用例触达分支的 `NameError`（修复 #11），并已补用例覆盖该分支。

**未声称**：真实能力内化（触发① 沿用 ≥20 样本门槛）、P7.2-10 组装注入已接通、企业多工作区形态已全量覆盖、审计台账保留策略已实现（见遗留 L8）。
