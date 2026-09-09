# TASK-S4-03 熔断 / 降级 / 整包回滚原子单位 + Saga 补偿（对齐 §4.4/§4.6/§5.7 注入防御）

> 所属阶段：S4 治理与安全｜依赖：S2-02（审计）、S4-01（审批）｜预估：5–8 人日
> 来源设计文档：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §4.4（L1-L5 自愈 + 整包回滚 P7.2-15 + 分裂脑 P7.2-16）/§4.6（Saga 补偿）/§5.7（提示注入防御六机制）
> 验收报告归档于本目录 `docs/zh/CloudPivot_v7.2重构计划/`

---

## 一、目标描述

对齐 v7.2 自愈/回滚/补偿语义到云枢既有自愈设施（`agent/self_healing/` 已有五层健康探针、熔断降级统一、失败路径反思闭环——**复用为主，补齐语义**）：

1. **L1-L5 语义对齐**：L1 重试≤2→降级→人工｜L2 劣化自动 downgrade｜L3 kill→git revert→重启→负面样本｜L4 journal 补偿→快照｜L5 租户级回滚+最高告警——映射到现有 self_healing 层级，补齐命名与触发对齐。
2. **整包回滚原子单位（P7.2-15）**：回滚 = code + skills + weights + data-baseline + manifest 的**整体 hash**；禁止只回技能不回代码（四套版本空间耦合）——在 release/发布设施上定义整包快照与回滚入口。
3. **Saga 补偿事务（§4.6）**：prepare（前置快照+意图哈希入 journal）→ execute → confirm；失败重放 compensating_action → aborted → 升级 L4；journal 每条 {saga_id, step, intent_hash, before_hash, after_hash, ts, trace_id}。
4. **分裂脑防护（P7.2-16）**：单机模式仅一个 Watchdog（lockfile 强制，云枢已有 lock_watchdog）；集群化入 P5。
5. **注入防御六机制接线（§5.7）**：taint 标记外来文本、指令/数据分离（工具参数仅云枢决策层生成）、能力最小暴露（子代理工具裁剪，配合 S4-04）、出域链路监测（配合 S4-02）、人机边界词（push --force 等 UI 显式确认）、UI 安全渲染（HTML 白名单/script 禁/CSP——配合 S6）。

## 二、执行步骤

### 步骤 1：现状盘点与语义映射
- 盘点 self_healing 现有层级/触发（五层健康探针归一化、熔断降级统一任务 3、失败路径反思任务 4、状态快照任务 5、D8 诚实化）与 monitoring lock_watchdog。
- 输出 `自愈语义映射表`（v7.2 L1-L5 ↔ 云枢现有层级 ↔ 缺口）。

### 步骤 2：整包回滚原子单位
- 定义 `ReleaseBundle`（code 版本 + skills 集版本 + weights/embeddings 基线 + data-baseline + manifest 的整体 hash）——在 release/快照设施（EVO snapshot、p6_snapshot、发布流程）之上加整包快照与 `rollback_bundle(bundle_hash)` 入口。
- 部分回滚防护：检测"只回技能不回代码"类不一致 → 拒绝 + 触发 L4 告警（§4.4 直接触发 L4）。

### 步骤 3：Saga 补偿
- 新增 `agent/self_healing/saga.py`（或复用 journal 设施）：`Saga`（prepare/execute/confirm + journal.log）、`compensate(saga_id)`（重放 compensating_action——消费 S1-02 descriptor.governance.compensating_action，risk≥high 操作强制走 Saga）。
- 高风险操作（descriptor risk ≥ high）执行前强制 prepare + 意图哈希；undo_hint 必须指向真实可执行命令（S1-02 已保证存量 risk≥high 带真实补偿描述）。

### 步骤 4：注入防御接线（§5.7 六机制）
- taint：外来文本（MCP 返回/检索结果/子代理输出/文件内容）打 taint 标记，禁入 system prompt 与决策分支（落地到上下文组装输入点，配合 ContextAssembler 已有集成——S5 联动）。
- 指令/数据分离：工具调用参数仅由决策层生成（审计既有拼接受污路径，单测覆盖）。
- 能力最小暴露：sub_agent 工具集裁剪清单（配合 S4-04）。
- 人机边界词：转账/发布/删库/改权限/push --force → UI 显式确认 + 60s 时效（永不自动化五类 §7）——接线到工具执行前置。
- UI 安全渲染（配合 S6 前端，本任务出后端约束与校验）。

### 步骤 5：回归与归档
- 回归：self_healing/monitoring/skills 相关套件零回归；新增单测（L1-L5 映射、整包 hash 与部分回滚拒绝、Saga 补偿与升级 L4、taint 禁入决策、人机边界词、分裂脑 lockfile）≥50 例、覆盖率 ≥80%。
- 混沌演练：kill 主进程/删快照/断网/注入篡改（§11.10）至少 2 项实际跑通并记录。
- 撰写 `TASK-S4-03_验收报告.md`。

## 三、预期成果

1. `自愈语义映射表`（v7.2 L1-L5 ↔ 云枢层级）。
2. ReleaseBundle 整包快照 + rollback_bundle + 部分回滚拒绝。
3. `agent/self_healing/saga.py`（Saga/journal/compensate，risk≥high 强制）。
4. 注入防御六机制接线（taint/指令数据分离/边界词/出域监测配合项）。
5. 混沌演练 2 项记录 + `TASK-S4-03_验收报告.md`。

## 四、评估标准（验收清单）

- [ ] L1-L5 语义映射表逐级对齐，无"同名不同义"残留
- [ ] 整包回滚：ReleaseBundle 整体 hash 生效；只回技能不回代码被拒绝并触发 L4 告警
- [ ] Saga：prepare/execute/confirm 三态 + journal 字段齐（saga_id/step/intent_hash/before/after/ts/trace_id）；补偿失败升级 L4
- [ ] risk≥high 操作强制 Saga 前置（无强制即拒绝执行）
- [ ] taint 外来文本无法进入 system prompt/决策分支（注入用例）
- [ ] 人机边界词（push --force 等）触发 UI 显式确认 + 60s 时效
- [ ] 单机 lockfile 防分裂脑（第二个 Watchdog 实例被拒）
- [ ] 既有 self_healing/monitoring/skills 套件零回归；新增单测全绿、覆盖率 ≥80%
- [ ] 混沌演练 ≥2 项实际跑通（kill 主进程 / 注入篡改至少一项）并记录
