# TASK-06：新颖性感知学习管线

## 0. 任务标识

| 字段 | 值 |
|---|---|
| 任务编号 | TASK-06 |
| 所属阶段 | 并行轨道（感知侧学习） |
| 前置依赖 | TASK-02（记忆回写能力，NoveltyEvent 需要写入记忆/检索面） |
| 并行建议 | 可与 TASK-03、TASK-04、TASK-07 并行 |
| 涉及主文档 | `docs/zh/智能体学习机制重构计划/智能体学习机制理想设计.md`（§3.2 D9 替代方案；§4.4 断点 6） |

## 1. 背景（为什么做）

设计思路明确要求"新颖性检测触发机制：当智能体遇到与已知经验差异超过阈值的新情境时，自动触发新颖性处理管线（表征→交互→增量学习更新）"。

审计发现：`sensor/change_detector.py`（`ChangeDetector`）已实现设备/磁盘/进程/服务/系统信息/注册表/环境的快照 diff（SHA-256 指纹比对）并持久化到 `~/.Yunshu/changes/change_log.json`，`sensor/behavior_sensor.py` 已建立 6 维度行为基线——但**全部是"日志器"**：diff 结果只转成 `SensorReading` 流向提示词注入，**从不触发任何学习/记忆/沉淀流程**（`sensor/` 下检索 novelty/learn/触发学习 零命中）。

另外：行为基线**不跨会话持久化**（每次启动重建），无法发现"长期行为漂移"；`change_log.json` 无容量上限与清理策略。

## 2. 目标描述（做什么）

1. 建立 **NoveltyEvent 管道**：ChangeDetector 的 diff 结果 → 分类 → 低置信写记忆 / 高置信产"技能或工作流建议"（走 TASK-04 审核链）。
2. **行为漂移检测**：行为基线跨会话持久化，周级对比检测漂移并产学习信号。
3. 全部默认**观察模式**（只记录不沉淀），显式开启后按分级策略沉淀。

## 3. 不变式约束（不易——禁止触碰）

- **禁止修改** `ChangeDetector` 的 diff 逻辑与 `SensorReading` 数据模型（只在其 collect 出口追加钩子）。
- **禁止修改** `sensor/registry.py` 的传感器发现/排除逻辑。
- **禁止修改** `behavior_sensor.py` 的 6 维度基线采集逻辑（只增加持久化与对比）。
- **保留** `~/.Yunshu/changes/change_log.json` 现有格式（新增字段可加，旧字段不动）。
- **保留** 既有认知层行为：SensorReading 流向 Translator 的既有路径不受影响（学习钩子是旁路）。
- 默认开关 `sensor_learning.enabled: false`（观察模式），开启后**只产 DRAFT 草稿**（不得直接注册技能）。

## 4. 执行步骤

### Step 1：NoveltyEvent 模型与分类
新增 `agent/learning/novelty.py`（或放 sensor 侧若架构允许；遵单向依赖：sensor→agent 允许则放 agent/learning，否则放 sensor/learning_hooks.py）：
- `NoveltyEvent` dataclass：`event_type`（hardware_change / process_change / file_change / behavior_drift）、`severity`、`diff_summary`、`confidence`、`suggested_action`、`created_at`。
- 分类器：将 ChangeDetector 的 diff 映射为事件类型与置信度（规则：硬件变更→高置信；进程新增/移除→中；文件批量变更→低；行为漂移→中）。
- `change_log.json` 增加容量控制：新增 max_entries（默认 10000）与清理策略（超出滚动删除最旧），并兼容旧文件。

### Step 2：事件→记忆/沉淀钩子
- 在 `ChangeDetector.collect()` 出口（或 `BodySensor.collect_all` 汇聚处）挂学习钩子（config 开关 `sensor_learning.enabled`）：
  - **低置信事件** → 写记忆：`MemoryManager.add_message`（带 `event` 标签）或 `lifetrace.SourceTree.record_*`（既有 record_sensor 路径），供后续对话引用；
  - **高置信事件** → 产**建议草稿**：生成"环境变化提示"（供 TASK-04 管道作为 Skill 建议的输入，本期只落 `data/learning/novelty_suggestions/` 草稿目录 + 审计日志，不自动注册）。
- 全部钩子 try/except 兜底；感知采集主路径零影响。

### Step 3：行为漂移跨会话检测
- `behavior_sensor.py` 基线增加持久化：存 `~/.Yunshu/baselines/behavior_<date>.json`（每周一份，保留 N=8 周滚动）。
- 新增周级对比任务（挂 TASK-04/05 同一调度收口）：对比最近两份基线，偏差超阈值（config `sensor_learning.drift_threshold`，默认 0.3）→ 产 `behavior_drift` NoveltyEvent（记录 + 草稿，同 Step 2 分级）。

### Step 4：补测试（TDD）
新增 `tests/unit/test_novelty_pipeline.py` + `tests/unit/test_behavior_drift.py`：
- 分类：构造 4 类 diff 样例断言事件类型/置信度/分级正确。
- 钩子：开关关 → 零写入；开关开 + 低置信 → 记忆出现该事件；高置信 → 草稿目录出现建议文件（不注册技能）。
- 持久化：基线写入/滚动清理正确；漂移超阈值产事件，未超不产。
- 容量：change_log 超上限正确滚动。
- 降级：记忆/检索面写入抛错时感知主链路正常。

### Step 5：回归与门禁
- `python -m pytest tests/unit -q` 全绿；新用例全绿；质量门禁见 §6。

## 5. 预期成果（交付物）

1. `NoveltyEvent` 模型 + 分类器 + 学习钩子（默认关闭）。
2. 行为基线跨会话持久化 + 周级漂移检测任务。
3. `change_log.json` 容量控制与清理。
4. 配置：`sensor_learning.enabled` / `sensor_learning.drift_threshold` / `sensor_learning.baseline_retention_weeks` / `sensor_learning.change_log_max_entries`（含注释，默认关闭）。
5. 测试：2 个新测试文件（≥ 12 用例）。
6. 变更说明：`docs/zh/智能体学习机制重构计划/变更说明/TASK-06_变更说明.md`。

## 6. 评估标准（验收条件）

### 功能验收
- [ ] 模拟一次磁盘分区/进程新增变更：`enabled=false` 零学习副作用（仅既有日志）；`enabled=true` 低置信事件入记忆可查。
- [ ] 高置信事件（如硬件变更）→ 草稿目录出现建议文件，且 skills_mgmt 无新增 PUBLISHED。
- [ ] 连续两次模拟采集构造漂移 > 阈值 → 产 `behavior_drift` 事件；低于阈值不产。
- [ ] `change_log.json` 达到上限后自动滚动，旧条目被清理且不破坏格式。
- [ ] 感知主链路（collect_all → Translator 注入）在钩子全部异常时零影响（回归验证）。

### 测试要求
- [ ] 新增 ≥ 12 用例全部通过；`python -m pytest tests/unit -q` 全绿。

### 质量门禁
- [ ] `python scripts/pre_commit_ci_guard.py --static-only --strict` 零新增告警。
- [ ] `python -m agent.observability.arch_rules --check` 通过（sensor 侧引用方向单向，避免 sensor→agent 上层反向依赖造成的循环；必要时钩子以回调/事件总线解耦）。

## 7. 工程约束（仓库规则）

- 同 TASK-01 §7（git 精确路径、commit -F、hook 环境变量、勿碰并行会话文件、UTF-8 无 BOM）。
- `sensor/` 目录改动需格外谨慎（Windows EventMonitor/WMI 线程敏感），新增代码不得改动既有采集线程的启动方式。
- 行为基线文件路径与格式在变更说明中写死，供后续任务对接。
