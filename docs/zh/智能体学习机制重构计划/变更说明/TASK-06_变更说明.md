# TASK-06 变更说明：新颖性感知学习管线

| 字段 | 值 |
| --- | --- |
| 任务编号 | TASK-06 |
| 所属阶段 | 并行轨道（感知侧学习） |
| 涉及主文档 | `docs/zh/智能体学习机制重构计划/智能体学习机制理想设计.md`（§3.2 D9 替代方案；§4.4 断点 6） |
| 关联任务书 | `docs/zh/智能体学习机制重构计划/TASK-06_新颖性感知学习管线.md` |
| 实现日期 | 2026-08-14 |

## 1. 背景

设计思路明确要求"新颖性检测触发机制：当智能体遇到与已知经验差异超过阈值的新情境时，自动触发新颖性处理管线（表征→交互→增量学习更新）"。

审计发现 `sensor/change_detector.py`（`ChangeDetector`）已实现设备/磁盘/进程/服务/系统信息/注册表/环境的快照 diff（SHA-256 指纹比对）并持久化到 `~/.Yunshu/changes/change_log.json`，`sensor/behavior_sensor.py` 已建立 6 维度行为基线——但全部是"日志器"：diff 结果只转成 `SensorReading` 流向提示词注入，从不触发任何学习/记忆/沉淀流程。且行为基线不跨会话持久化，`change_log.json` 无容量上限与清理策略。

本任务补齐感知侧学习闭环：NoveltyEvent 管道（diff → 分类 → 分级沉淀）、行为漂移跨会话检测、日志容量控制。全部默认**观察模式**（`sensor_learning.enabled: false`），开启后只产 DRAFT 草稿，绝不注册技能。

## 2. 改动点

### 2.1 新增 `sensor/novelty.py`（Step 1 模型 + 分类器 + 容量控制）

纯数据/纯函数模块，零运行时依赖（yaml 延迟导入），**不 import agent**（sensor 是底层包，禁止反向依赖，arch_rules 防循环）。

**`NoveltyEvent` dataclass**：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `event_type` | str | `hardware_change` / `process_change` / `file_change` / `behavior_drift` |
| `severity` | str | 透传源 diff 条目的 severity（normal/warning/critical） |
| `diff_summary` | str | diff 摘要 |
| `confidence` | float | 置信度（分类器规则产出） |
| `suggested_action` | str | 建议动作文案（供 TASK-04 审核链作为建议输入） |
| `created_at` | str | ISO 时间（默认当前时刻） |
| `detail` | dict | 附加明细（behavior_drift 含 `drift_score`） |

`level` 属性 = 置信度分级：≥0.7 → `high`；≥0.4 → `medium`；<0.4 → `low`。

**分类器**（`classify_change` / `classify_changes`），映射真实 ChangeDetector type 集合：

| diff type（含 `hardware_*` 前缀） | 事件类型 | 置信度 | 分级 |
| --- | --- | --- | --- |
| `device_added/removed/modified`、`disk_mounted/unmounted`、`hardware_*` | `hardware_change` | 0.85 | high |
| `process_started/stopped`、`service_state_changed` | `process_change` | 0.55 | medium |
| `file_added/modified/removed/changed` | `file_change` | 0.30 | low |
| `behavior_drift` | `behavior_drift` | 0.50 | medium |
| `registry_changed`、`environment_changed`、`system_info_changed` 等噪音 | — | — | 不学习（None） |

**容量控制**（`trim_change_log` / `default_max_entries`）：超上限滚动删除最旧；`max_entries≤0` 或未设 → 原样返回；默认 10000（env `SENSOR_LEARNING_CHANGE_LOG_MAX_ENTRIES` > config `learning.sensor_learning.change_log_max_entries` > 10000）。

**行为漂移量化**（`week_key` / `compute_drift_score` / `detect_behavior_drift`）：漂移度 = 重叠指标相对偏差均值（`|cur-prev|/prev`，基线为 0 或非数值跳过，无样本返回 0.0）；≥ 阈值产 `behavior_drift` 事件（中置信 0.5，`detail.drift_score` 落盘），缺基线/低于阈值返回 None。

### 2.2 `sensor/change_detector.py` 追加钩子与容量配置（Step 1，不改 diff 逻辑）

- 构造函数新增 `learning_hook`（回调，接收 changes 列表）、`max_entries`（容量上限）、`persistent_log_dir`（测试隔离用；既有调用方无参/关键字调用，签名向后兼容）三个参数；
- `collect()` 出口**旁路**触发钩子（`_invoke_learning_hook`）：异常 try/except 兜底，感知主链路零影响；不改变 diff 逻辑与 SensorReading 生成（【不易】约束）；
- `set_learning_hook(hook)` / `attach_change_learning_hook` 挂载/替换/解除；
- `_load_persistent_log` / `_save_to_persistent_log` 容量控制改为 `max_entries` 可配置（替代原硬编码 10000），并兼容旧文件（纯数组 / `{"entries": [...]}` / 损坏回退空）。

### 2.3 新增 `agent/learning/novelty_hooks.py`（Step 2 沉淀钩子）

- **分级路由**（`handle_novelty_event`）：低/中置信（<0.7）→ 写记忆（`data/learning/novelty_memory/novelty_memory.jsonl`，JSONL，`event=novelty_event` 标签）；高置信（≥0.7）→ 审计（`data/learning/novelty_audit.jsonl`）+ 建议草稿（`data/learning/novelty_suggestions/novelty_suggestion_<type>_<ts>.json`，**`draft_status: "DRAFT"`**，供 TASK-04 审核链作为输入，绝不注册技能）；
- `make_learning_hook()` 构造 ChangeDetector 出口钩子（默认观察模式：未开启 `sensor_learning.enabled` → 零副作用直接返回）；`wire_body_sensor()` 挂载到 BodySensor（旁路，失败仅日志）；
- 全部动作独立 try/except 兜底，感知采集主路径零影响（记忆目录写入失败 → 仅 WARNING）；
- 配置优先级：env > config.yaml > 硬编码默认值（`enabled` / `drift_threshold` / `baseline_retention_weeks` / `draft_dir` / `audit_file` / `memory_dir`）。

### 2.4 `sensor/behavior_sensor.py` 基线持久化 + 新增 `agent/learning/behavior_drift.py`（Step 3 漂移检测）

**行为基线持久化**（不改 6 维度 `_collect_*` 采集逻辑，仅新增数据面方法）：

| 方法 | 说明 |
| --- | --- |
| `capture_baseline()` | 采集当前行为基线（汇总 `collect()` 输出中的数值指标，跨会话可比） |
| `save_baseline(dir, retention_weeks)` | 保存当前周基线 + 超保留期滚动清理 |
| `list_baselines(dir)` / `load_baseline(week, dir)` | 按周键列出/加载 |
| `_prune_baselines(dir, retention_weeks)` | 保留最近 N 周，删除更旧（仅删 `behavior_*.json`） |

**基线文件路径与格式（写死，供后续任务对接）**：

```
目录: ~/.Yunshu/baselines/
文件: behavior_<周一日期>.json   （每周一份，ISO 周键 YYYY-MM-DD，如 behavior_2026-08-10.json）
内容: {"week": "2026-08-10", "captured_at": "<ISO>", "metrics": {指标名: 数值}}
保留: 8 周滚动（config learning.sensor_learning.baseline_retention_weeks）
```

**周级漂移调度器**（`BehaviorDriftScheduler`，挂 TASK-04/05 同一调度收口）：

| 方法 | 说明 |
| --- | --- |
| `schedule(*, interval_hours=168) -> Dict` | 默认关闭（`sensor_learning.enabled=false` → `status=disabled` 零副作用）；开启后 `task_scheduler.add_interval_task` 注册周级任务 |
| `unschedule() -> bool` | 按固定任务名 `行为漂移检测` 注销（可跨实例） |
| `run() -> Dict` | 采集→保存当前周基线→对比最近两份→超阈值产 `behavior_drift`（记忆记录 + 建议草稿，仅 DRAFT）；基线不足两份 → `skipped`；低于阈值 → `no_drift` |

### 2.5 接线（旁路，默认零副作用）

- `agent/orchestrator/lifecycle_manager.py`：BodySensor 初始化后 `wire_body_sensor(self.body)`（try/except 兜底，挂载失败不影响感知初始化）；
- `agent/skills_mgmt/learning_scheduler.py`：`register_learning_schedulers()` / `unregister_learning_schedulers()` 统一收口 `behavior_drift` 注册/注销。

### 2.6 配置（config.yaml，含注释；.env.example 同步）

| 配置键 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `learning.sensor_learning.enabled` | `SENSOR_LEARNING_ENABLED` | `false` | 总开关（观察模式；开启后低置信→记忆、高置信→建议草稿，仅 DRAFT） |
| `learning.sensor_learning.drift_threshold` | `SENSOR_LEARNING_DRIFT_THRESHOLD` | `0.3` | 行为漂移判定阈值（周级基线相对偏差均值） |
| `learning.sensor_learning.baseline_retention_weeks` | `SENSOR_LEARNING_BASELINE_RETENTION_WEEKS` | `8` | 行为基线保留周数（滚动清理） |
| `learning.sensor_learning.change_log_max_entries` | `SENSOR_LEARNING_CHANGE_LOG_MAX_ENTRIES` | `10000` | change_log.json 容量上限 |
| `learning.sensor_learning.draft_dir` | `SENSOR_LEARNING_DRAFT_DIR` | `./data/learning/novelty_suggestions` | 建议草稿目录 |
| `learning.sensor_learning.audit_file` | `SENSOR_LEARNING_AUDIT_FILE` | `./data/learning/novelty_audit.jsonl` | 学习审计日志 |
| `learning.sensor_learning.memory_dir` | `SENSOR_LEARNING_MEMORY_DIR` | `./data/learning/novelty_memory` | 事件记忆目录 |

## 3. 裁决记录（决策/适配）

| 编号 | 裁决 | 依据 |
| --- | --- | --- |
| R1 | **模型与分类器放 `sensor/novelty.py`（sensor 侧）而非 `agent/learning/`**：任务书 Step 1 允许"遵单向依赖"二选一。sensor 是底层包，`change_detector`/`behavior_sensor` 均需引用事件模型与容量函数；若放 agent 侧会造成 sensor→agent 反向依赖（违反 arch_rules 防循环）。放 sensor 侧后 agent 侧正常引用（agent→sensor 合法单向）；配置读取在 novelty.py 内自含（env > 直接读 config.yaml > 默认值），不 import agent | arch_rules `no_circular_dependency` + 任务书 Step 1 单向依赖约定 |
| R2 | **`change_log.json` 容量上限可配置**：原 `_save_to_persistent_log` 硬编码 10000。改为 `max_entries`（默认按配置 10000），保持旧文件格式兼容（纯数组 / dict-with-entries 均可加载，损坏回退空） | 任务书 Step 1"新增 max_entries 与清理策略，并兼容旧文件" |
| R3 | **学习钩子以回调旁路注入，不动 `collect()` diff 循环**：钩子在 changes 循环完成后、return 前触发；异常由 `_invoke_learning_hook` 兜底。验收"感知主链路在钩子全部异常时零影响"由 `test_learning_hook_memory_failure_isolated` / `test_change_detector_hook_exception_safe` 覆盖 | 任务书 §3 不变式（禁止修改 diff 逻辑）+ §4 Step 2 |
| R4 | **行为漂移事件分级**：任务书分类规则"行为漂移→中"与 Step 2 分级路由（低→记忆、高→草稿）存在张力。裁决：`behavior_drift` 中置信（0.5）走"记忆记录"，调度器 `run()` 显式补一次 `_write_draft`（记录 + 草稿，验收条件原文"产 behavior_drift 事件（记录 + 草稿）"），仍仅 DRAFT 不注册技能 | 任务书 §4 Step 3 验收 + §2 目标 2 |
| R5 | **记忆面写入策略**：低/中置信事件写 JSONL 事件记忆（`data/learning/novelty_memory/`，`event=novelty_event` 标签）而非 `MemoryManager.add_message`/`lifetrace`——后者属会话记忆/认知层，写入需走既有注入路径，风险高且无标签语义；JSONL 事件记忆轻量、可检索、可被后续对话引用（TASK-02 记忆回写能力对接面），且全部兜底不影响感知主链路 | 任务书 Step 2"带 event 标签"（二选一允许）+ 最小改动原则（不易） |

## 4. 测试

| 文件 | 用例数 | 覆盖 |
| --- | --- | --- |
| `tests/unit/test_novelty_pipeline.py` | 16 | 4 类 diff 分类（类型/置信度/分级）；未知类型不学习；level 边界；change_log 容量滚动 + 旧格式兼容；钩子开关关零副作用 / 低置信写记忆 / 高置信草稿（仅 DRAFT）+ 审计；记忆写入抛错降级；ChangeDetector collect 出口钩子旁路 + 钩子异常兜底 |
| `tests/unit/test_behavior_drift.py` | 13 | week_key（周一对齐）；漂移量化（无重叠 0 / 相对偏差 40→80=1.0、40→42=0.05）；超阈值产事件 / 低于阈值与缺基线不产；基线保存/列表/加载/保留期滚动清理；调度器默认关闭不注册 / 开启注册（interval 生效）/ 基线不足 skipped / 漂移超阈值产事件（记忆+草稿 DRAFT）/ 未超不产 |

合计 29 用例（≥ 任务书 12 用例要求）。

**回归结果（2026-08-14）**：
- 新增 2 文件：`29 passed`（含修复 `behavior_sensor.py` 顶层缺 `import json` 的运行时缺陷——TASK-06 新增的 save/list/load 基线方法使用了 json 但未导入，导致 4 个用例失败，补导入后全绿）；
- **`tests/unit/test_learning_scheduler.py` 契约同步（3 用例）**：TASK-06 在 `learning_scheduler.py` 统一收口新增 `behavior_drift`（第 4 项），该 TASK-05 测试原断言 3 键/3 任务名致 3 failed；已同步更新（`_MODULES`/`TASK_NAMES` 加 `behavior_drift`，开关按模块差异分别 patch `_enabled`/`_sensor_learning_enabled`），修复后 `3 passed`；
- 定向回归（引用 sensor 模块的既有单元测试 9 文件 + sensor/test_body_sensor.py）：TASK-06 相关全部通过；
- `sensor/test_body_sensor.py::test_collect_all` **既有环境性失败（非本任务引入）**：`body_sensor.py _apply_tags` 收到 `window_sensor.py` 返回的 dict（L204 无前台窗口时直接返回 dict），该文件与 `_apply_tags`/`collect_all` 在 HEAD 均未改动（git diff 仅 `attach_change_learning_hook` 纯新增）；系本机无活动前台窗口 + 并行会话负载所致，不属 TASK-06 范围（详见 §6）；
- 全量 `pytest tests/unit -q -p no:randomly -m "not slow"`（快速回归，11633 用例）：结果见 §6 回归记录；
- `python -m agent.observability.arch_rules --check`：✅ 通过，未豁免违规 0（既有 4 项豁免与本任务无关），sensor→agent 无反向依赖（novelty.py 零 agent 引用）；
- `pre_commit_ci_guard --static-only --strict`：FAIL=0，新增阻断 WARN=0（47 条存量 WARN 全在基线内豁免）。

## 5. 回滚方法

1. **代码回滚**：删除 `sensor/novelty.py`、`agent/learning/novelty_hooks.py`、`agent/learning/behavior_drift.py`；`git checkout` 还原 `sensor/change_detector.py`、`sensor/behavior_sensor.py`、`sensor/body_sensor.py`、`agent/orchestrator/lifecycle_manager.py`、`agent/skills_mgmt/learning_scheduler.py`、`config.yaml`、`.env.example`；
2. **运行时开关**：`learning.sensor_learning.enabled` 保持 `false`（默认关闭）即零学习副作用，钩子/漂移任务内部直接返回（观察模式）；
3. **产物清理**：已产建议草稿（`data/learning/novelty_suggestions/`）为 DRAFT 文件可安全删除；事件记忆/审计 JSONL 可删可留；行为基线 `~/.Yunshu/baselines/behavior_*.json` 可删（下次开启自动重建）。

## 6. 工程约束落实

- **不变式逐项核对**：`ChangeDetector` diff 逻辑未改（仅出口旁路钩子）；`SensorReading` 模型未改；`sensor/registry.py` 未触碰；`behavior_sensor.py` 6 维度 `_collect_*` 采集逻辑未改（仅新增持久化方法 + 顶层 `import json` 修复）；`change_log.json` 旧格式兼容（纯数组/dict-with-entries/损坏回退）；SensorReading→Translator 既有路径零影响（钩子旁路）；
- **行为基线文件路径与格式**已在 §2.4 写死（`~/.Yunshu/baselines/behavior_<周一日期>.json`，`{"week","captured_at","metrics"}`，保留 8 周），供后续任务对接；
- **sensor/ 谨慎改动**：未改动既有采集线程启动方式；新增钩子为 collect() 内同步调用（不新开线程），EventMonitor/WMI 线程不受影响；
- **并行会话风险**：实现期间并行会话（worktree pr634_fix2）活跃，共享 index 有清空/混入风险，本任务 5 个新文件（sensor/novelty.py、agent/learning/×3、tests×2）均为 untracked 新增不覆盖既有文件；既有文件改动（change_detector/behavior_sensor/body_sensor/config.yaml/.env.example）改后已 Read/Grep 核验关键标记；
- **`sensor/test_body_sensor.py::test_collect_all` 环境性失败记录**：`window_sensor.collect()`（L204）在无前台窗口时返回 dict，`collect_all()` L516 `results.append(data)` 后 `_apply_tags` L443 访问 `r.tags` 报 `AttributeError`。该文件与 `collect_all`/`_apply_tags` 代码在 `HEAD` 与工作区逐字节一致（`git diff sensor/body_sensor.py` 仅 `attach_change_learning_hook` 新增；`git show HEAD:...` 比对 `_apply_tags` 相同），且与 novelty/钩子路径无关——判定为既有真实硬件采集环境缺陷，不在 TASK-06 范围（修 `window_sensor` 属超出本任务的最小改动边界，留待感知专项）。

### 回归记录（占位，全量快速回归完成后更新）

- `pytest tests/unit -q -p no:randomly -m "not slow"`（11633 用例，后台执行）：待完成。
