# TASK-S4-03 自愈语义映射表（v7.2 §4.4 L1-L5 ↔ 云枢既有层级）

> 交付物 1／任务书 §三.1｜生成来源：`agent/self_healing/levels.py::render_mapping_markdown()`
> （**表由代码生成，不是手抄**——下述表格可直接用 `python -c "from agent.self_healing.levels import render_mapping_markdown as f; print(f())"` 复现）
> 基线：`master`｜worktree：`s403`

---

## 一、术语纪律：两套「五层」不是一回事（验收项「无同名不同义残留」）

云枢里存在**两套编号 L1-L5 的东西**，语义正交。本任务是"熔断回滚与 Saga"，
必须把这条界线写死，否则后续会话极易把二者混为一谈：

| | 健康探针五层（既有，`agent/health/probes.py`） | 自愈升级五级（本任务，`agent/self_healing/levels.py`） |
|---|---|---|
| 回答的问题 | **看哪里**（可观测性分层） | **做什么**（故障升级链） |
| 取值 | `l1_process` / `l2_dependency` / `l3_llm_tool` / `l4_business` / `l5_semantic` | `L1` / `L2` / `L3` / `L4` / `L5` |
| 组织依据 | 业务重要性加权（0.25/0.20/0.25/0.20/0.10，固定不可变） | 副作用强度与影响半径递增 |
| 数量巧合 | 5 层 | 5 级 |

**编号重合纯属巧合，语义彼此独立**：一次 L1（重试）可能要读 `l3_llm_tool` 探针，
一次 L5（租户回滚）也可能由 `l1_process` 探针触发。

这条纪律有**机器可读断言**：`agent/self_healing/levels.py::assert_no_level_confusion()`
（探针层名一律小写带语义后缀、自愈级一律 `L<n>` 纯编号；交叉冒充即断言失败），
并由 `tests/unit/test_self_healing_levels.py` 持续看住。

---

## 二、映射表（代码生成）

"云枢既有落点"一列写的是**执行归属**——本任务不新建自愈栈，动作一律落到既有设施；
"缺口"一列如实标注哪些是既有能力、哪些此前完全缺失。

| v7.2 级别 | 语义（§4.4 逐字） | 云枢既有落点 | 触发 | 影响半径 | 告警 | 自动化 | 审批 | 负面样本 | 缺口 |
|---|---|---|---|---|---|---|---|---|---|
| **L1** 受限重试 → 降级 → 人工 | L1 重试≤2→降级→人工 | `agent.self_healing.policy::RESTORE_MAP[llm_timeout].actions`<br>`agent.monitoring.self_healer::SelfHealer.execute_action`<br>`agent.observability.model_degrade::call_with_model_fallback`<br>`agent.error_handler` | 瞬态失败且未验证为持续性劣化（error_handler.category ∈ transient/timeout） | process | info | 是 | 否 | 否 | 既有 `retry_limited`/`degrade_llm_router` 在 RESTORE_MAP 中标 `unimplemented`；本任务只做**语义对齐与升级判定**，不代实现该两动作（仍由既有 SKIPPED 语义承接）。 |
| **L2** 劣化自动降级 | L2 劣化自动 downgrade | `agent.graceful_degrade::GracefulDegrade`<br>`agent.observability.model_degrade::handle_primary_failure`<br>`agent.skills_mgmt.rollback::AutoRollback.check_and_rollback`<br>`agent.monitoring.self_healer::SelfHealer` | 同类失败达阈值或质量/延迟相对基线劣化（非单次瞬态） | component | warning | 是 | 否 | 否 | 既有 AutoRollback 面向 skill 版本；模型/组件降级分别由 graceful_degrade 与 model_degrade 承担，二者此前无统一「L2」口径——本任务补齐口径，不合并实现。 |
| **L3** 杀进程 → 回退代码 → 重启 → 负面样本 | L3 kill→git revert→重启→负面样本 | `agent.monitoring.self_healer::SelfHealer._restart_service`<br>`agent.monitoring.self_healer::SelfHealer.verify_action`<br>`agent.evolution.defect_case::build_failure_case`<br>`agent.monitoring.alert_manager::AlertManager` | L2 降级后仍劣化，或验证失败连续达阈值（SelfHealer._verify_failure_counts） | host | high | 是 | 否 | 是 | 既有 SelfHealer 已有 restart/verify 与失败升级回调；「git revert」与「负面样本」此前分散（skills_mgmt.rollback / evolution.defect_case），本任务只做链接与命名。 |
| **L4** journal 补偿 → 快照恢复 | L4 journal 补偿→快照 | `agent.self_healing.saga::Saga.compensate`<br>`agent.self_healing.release_bundle::rollback_bundle`<br>`agent.p6_snapshot::StateSnapshotManager`<br>`agent.monitoring.alert_manager::AlertManager` | Saga 补偿失败，或检测到整包回滚不一致（只回技能不回代码，P7.2-15） | bundle | high | 是 | 否 | 是 | 此前**完全缺失**：无 Saga journal、无整包回滚原子单位 → 本任务新增 saga.py 与 release_bundle.py（唯一实做级）。 |
| **L5** 租户级回滚 + 最高告警 | L5 租户级回滚+最高告警 | `agent.self_healing.release_bundle::rollback_bundle`<br>`agent.monitoring.alert_manager::AlertManager`<br>`agent.audit.facade::audit.record` | 影响面跨出租户边界，或 L4 快照恢复失败/事件涉及多租户数据一致性 | tenant | critical | **否** | 是 | 是 | 此前**完全缺失**：无租户级回滚入口与最高告警分级 → 本任务新增（集群化留 P5，P7.2-16 明示）。 |

---

## 三、映射关系的三条说明（评审要点）

### 1. 为什么是"叠加"而不是"重写"

任务书明示「映射到既有 self_healing 层级，补齐命名与触发（**勿另建自愈栈**）」。
`levels.py` 因此**不含任何执行逻辑**：它不 import `self_healer` / `p6_snapshot` /
`subprocess`，只提供
① 声明式规格（`LEVEL_SPECS`）、② 触发信号 → 级别的解析（`resolve_level`）、
③ 升级判定（`escalate`）、④ 事故卡成型与事件发射。
动作执行仍归 `agent/monitoring/self_healer.py`、`agent/graceful_degrade.py`、
`agent/observability/model_degrade.py`、`agent/p6_snapshot.py`、`agent.skills_mgmt.rollback`。

**可验证**：`grep -n "^from\|^import" agent/self_healing/levels.py` 只有标准库 +
本包 `saga`/`release_bundle` 的**延迟导入**（在函数体内），模块级零重依赖。

### 2. 既有触发与告警阈值零回归

L1-L5 是**新增的标注层**：`levels.py` 不注册告警规则、不改 `HealPolicy` 的
threshold/cooldown/max_per_hour、不动 `RESTORE_MAP`。
既有的 `SelfHealer._verify_failure_counts` 连续失败升级回调（→ `AlertManager.escalate`）
**仍按原样工作**；L4/L5 只是给它补上"补偿失败/跨租户"这两个此前没有的落点。

### 3. 两个此前**完全缺失**的级别（本任务实做）

| 级别 | 缺口 | 本任务补法 |
|---|---|---|
| **L4** | 云枢此前没有任何"做了一半的高危操作"的统一语义：`skills_mgmt/rollback.py` 回版本、`p6_snapshot.py` 存状态、审批只记录"谁批了"，但**没有任何地方记录"这一步打算做什么、做完前后状态是什么、失败后该补偿什么"** | 新增 `agent/self_healing/saga.py`（§4.6 journal 七元）+ `agent/self_healing/release_bundle.py`（§4.4 P7.2-15 整包回滚原子单位） |
| **L5** | 无租户级回滚入口、无"最高告警"分级 | `release_bundle.rollback_bundle` 的租户维度 + `levels._raise_max_alert`（走既有 `AlertManager.escalate` → critical 通知 + 人工接管条目）；集群化留 P5（P7.2-16 明示） |

---

## 四、事故卡与 MTTD/MTTR（本任务对下游的接口）

* **事故卡（`levels.IncidentCard`）**：逐字对齐 v7.2 §3 —— `{id, severity: L1-L5,
  root_cause, fatal_change, evasion_rule, in_strategy_memory, regression_case_added,
  trace_ids, mttd_ms, mttr_ms, status, created_at, resolved_at}`。
  **六要素齐才可 `resolve()`**（缺要素抛 `LevelError`，不静默降级）——
  这是"不可追溯的 97% 比没有数字更危险"（§7 UI 五坑⑤）的机制化。
  数据源：S6-01「自愈事故」面板 + §8.6 恢复向导。

* **`healing.triggered` 发射方（S5-02 数据源契约）**：S5-02 已在
  `agent/eval/metrics.py::compute_healing_latency` 定义 MTTD/MTTR 取自
  `healing.triggered` 的 `mttd_ms` / `mttr_ms` 字段（目标 MTTD < 3s / MTTR < 30s），
  但**此前无发射方**（指标只能出 `framework_only`）。本任务的
  `levels.emit_healing_triggered()` 即该发射方，载荷含
  `level` / `level_code` / `signal` / `scope` / `severity` / `mttd_ms` /
  `mttr_ms` / `tenant_id` / `incident_id`（**只放叶子，不放原始用户文本**）。

* **`escalation → 既有告警通道`**：L5 的最高告警**复用** `AlertManager.escalate()`
  （它已把 critical 通知 + 人工接管入队 `TakeoverRecord` 做全），
  而不是并行新发一条告警规则——否则同一事故会产生两套互不相关的人工接管记录。
