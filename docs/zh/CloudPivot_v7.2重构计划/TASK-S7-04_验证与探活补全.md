# TASK-S7-04 验证与探活补全（性能补测 + native 期探活设施）

> 所属阶段：**S7 稳态运营补完批次**｜依赖：S5-03（`PERF_BUDGET_REBASED.md` / `cost_brake`）、S3-02（判定集 + 沙箱）、S3-03（shadow/stage）、S4-03（自愈层级）｜预估：4–6 人日
> 来源：收官审计 #4（4 项性能无实测）+ #3（native 期探活未实现）+ Owner 决策 2026-09-12 选择推进
> 验收报告归档于本目录 `docs/zh/CloudPivot_v7.2重构计划/`

---

## 一、目标描述

本任务含两个**同属"把没验证的验证掉、把未来的闸门先建好"**的子项，可分步执行、分别验收：

### 子项 A（#4）性能预算补测：清掉 ❓
`PERF_BUDGET_REBASED.md` 中仍有 4 项**无实测**（如实标 ❓）。S6-01 已补测「状态灯渲染 p95 0.7ms」与「首屏 FCP/LCP 500ms」，**剩余待测**：
- **Watchdog `<10s`**（硬性指标）：主进程失联 → Watchdog 拉起/恢复感知时间；
- **熔断「达阈值 → 生效」`<3s`**：连续失败达阈值 → 熔断实际阻断 outbound 的时间；
- 另 2 项（以 `PERF_BUDGET_REBASED.md` §五 现列 ❓ 为准，逐项复测或给出测法）。

### 子项 B（#3）native 期探活设施：把"未来的闸门"先建好
v7.2 §3.3 要求 `native` 状态"30 天零回退 + 每周探活"，审计 T4 指出**探活内容未定义**。当前无 native 能力、时间也未到，但**设施可以先建**（等能力到达即自动生效）：
- 探活内容 = **判定集子集每周重放** + **成本/成功率监控**（对齐审计 T4 建议）；
- 退化 → 产出 **stage 回退建议**（`native → borrowed` 走回上游，不改代码）并**升级为事故卡**（对齐 S4-03 的 L2/L3 语义）。

## 二、执行步骤

### 步骤 1（子项 A）：受控环境实测
- 产出复测脚本 `scripts/measure_perf_budget.py`（可复现、可单跑，不依赖生产）：
  - Watchdog：在**受控沙箱**内模拟主进程心跳停止（**不得 kill 真实生产进程**）→ 记录 Watchdog 感知与恢复时间；
  - 熔断：以假实现/本地桩触发连续失败达阈值 → 记录"阈值达成"到"实际阻断"的时延；
  - 测试方法必须**可重跑**并输出原始数据（毫秒级采样，非估算）。
- 结果回填 `PERF_BUDGET_REBASED.md`：**实测值 + 采样方法 + 环境说明**；确实无法在单机测出的（如集群相关）保留 ❓ 并写明**测法与环境要求**（不得删指标了事）。

### 步骤 2（子项 B）：探活设施
- 新增 `agent/digestion/probe.py`（与消化链同包，复用其资产）：
  - `LivenessProbe.run(capability_id)`：取该能力**判定集子集**（默认按 `CaseSet` 抽样 N 组，`N` 可配）→ 用 `ReplaySandbox` 重放 → 记录通过率、p99（**真实墙钟**）、成本增量；
  - `evaluate_liveness(...)`：按阈值判定 —— ① 通过率 < 基线×0.98 ② p99 回归 > 阈值 ③ 成本显著上升 ④ 距上次成功探活 > 周期 → 任一命中即 `degraded`；
  - `suggest_stage_rollback(...)`：产出 `native → borrowed` **回退建议**（不自动执行 stage 变更；如需执行走既有 `stage_migrate` + 审计）；
  - 退化升级：调用 `agent/self_healing/levels.py::raise_incident` 产出事故卡（六要素齐才可 resolved）。
- **调度**：`register_liveness_job()`（每周；**默认关闭**，环境变量 `CP_DIGESTION_LIVENESS_ENABLED=true` 才注册——安全底线，与 shadow/重探一致）；
- **作用范围**：当前对 `internalized`/`native` 能力生效；无此类能力时 `run()` 返回"无可探活能力"（**不报错、不编造**）。
- 与 S3-02 的 30 天漂移重探**区分**：重探关注**上游契约漂移**，探活关注**自身实现退化**——两者在报告中并列说明，避免混淆。

### 步骤 3：回归与归档
- 新增单测（子项 A）：采样脚本可跑、输出结构与单位正确、环境不可测项如实标注；
- 新增单测（子项 B）：判定集子集重放、四项退化条件各自触发、回退建议产出（不自动执行）、事故卡生成、默认关闭、无能力时安全返回、阈值可配；
- 回归：`digestion`（含 shadow/internalize/gate）、`self_healing`、`cost_brake` 邻接套件零回归；
- 撰写 `TASK-S7-04_验收报告.md`（含实测原始数据与探活演示记录）。

## 三、预期成果

1. `scripts/measure_perf_budget.py` + `PERF_BUDGET_REBASED.md` 更新（实测值或"测法 + 环境要求"）。
2. `agent/digestion/probe.py`：`LivenessProbe` / `evaluate_liveness` / `suggest_stage_rollback` / `register_liveness_job`（默认关闭）。
3. 探活退化 → 事故卡 + stage 回退建议（不自动执行）。
4. `TASK-S7-04_验收报告.md`。

## 四、评估标准（验收清单）

**子项 A**
- [ ] Watchdog 感知/恢复时间有**实测值 + 采样方法 + 环境说明**（或如实标 ❓ 并给测法与环境要求）
- [ ] 熔断"达阈值 → 生效"时延有实测（同上）
- [ ] 复测脚本可重复运行，输出原始采样（非估算/非引用历史）
- [ ] 未删除任何既有指标项（只允许"填实测"或"标测法"）
- [ ] 未编造数字；无法测的明确写明原因

**子项 B**
- [ ] `LivenessProbe.run()` 对 `internalized`/`native` 能力跑判定集子集并产出通过率/真实墙钟 p99/成本增量
- [ ] 四项退化条件各自有触发用例；命中即 `degraded`
- [ ] 退化产出 **stage 回退建议**（`native→borrowed`）且**不自动执行** stage 变更（用例断言）
- [ ] 退化升级为事故卡（`raise_incident`），六要素齐备
- [ ] `register_liveness_job()` **默认关闭**，需显式环境变量才注册
- [ ] 无 `internalized`/`native` 能力时安全返回（不报错、不编造数据）
- [ ] 报告中明确区分"探活（自身退化）"与"漂移重探（上游契约）"两个概念
- [ ] `digestion`/`self_healing`/`cost_brake` 邻接套件零回归；新增单测全绿、覆盖率 ≥80%
