# START-S4-03 熔断回滚与 Saga（分发壳 · 可直接复制）

> **本文件不是任务书。** 任务规格 → [`TASK-S4-03_熔断回滚与Saga.md`](TASK-S4-03_熔断回滚与Saga.md)（★ 执行会话必须完整阅读）
> 批次与通用约定 → [`PARALLEL_并行铺开总表.md`](PARALLEL_并行铺开总表.md)
> 基线：`master` / `15eae00d`｜波次：**第二波**｜预估：5–8 人日
> ⚠ **两条前置纪律**：① 依赖 **S4-01**（审批落点）→ 待其结案或至少接口定稿后再开工；
> ② 落点 `agent/monitoring/` 与 **S5-03** 重叠、`agent/guardrails/` 与 **S4-02** 重叠 → 两者均在第一波先结案，本任务再开。

---

## 一、开工前确认

1. `--base master`；worktree id 用 **`s403`**。
2. **开工前置检查**：`S4-01`（审批矩阵/授权语义）已结案或其接口已定稿；`S5-03` 与 `S4-02` 已结案（避免 monitoring/guardrails 同文件并发）。
3. 无待裁定项。

---

## 二、启动提示词（整段复制给新会话）

```
【任务】TASK-S4-03 — L1-L5 自愈语义对齐 + 整包回滚原子单位 + Saga 补偿 + 注入防御六机制
【任务书】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\TASK-S4-03_熔断回滚与Saga.md（★ 必须先完整阅读）
【批次总表】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\PARALLEL_并行铺开总表.md
【所属计划】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\00_总览_审计结论与重构总计划.md
【上游设计文档】C:\Users\Administrator\Desktop\设计思路\CloudPivot_v7.2_final_合并归档(智谱审核版).md
                （相关章节：§4.4 L1-L5 自愈 + P7.2-15 整包回滚 + P7.2-16 分裂脑 / §4.6 Saga 补偿 / §5.7 注入防御六机制）
【预估】5–8 人日
【状态】第二波：依赖 S4-01（审批）；S5-03/S4-02 应先结案（文件落点重叠）

━━━ 一、工作区与隔离（强制）━━━
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s403 --base master
此后所有 git 操作在 .worktrees/s403/ 内；主工作区禁令与提交流程见批次总表 §三。
双远端推送：git push origin master && git push gitee master

━━━ 二、任务目标（摘要）━━━
1. **L1-L5 语义对齐**：L1 重试≤2→降级→人工｜L2 劣化自动 downgrade｜L3 kill→git revert→重启→负面样本｜
   L4 journal 补偿→快照｜L5 租户级回滚+最高告警——**映射到既有 self_healing 层级，补齐命名与触发**（勿另建自愈栈）
2. **整包回滚原子单位（P7.2-15）**：回滚单位 = 整包 release 的整体 hash（code + skills + weights + data-baseline + manifest）；
   **禁止只回技能不回代码**——检测到部分回滚 → 拒绝并触发 L4 告警
3. **Saga 补偿事务（§4.6）**：prepare（前置快照 + 意图哈希入 journal）→ execute → confirm；
   失败重放 compensating_action → aborted → 补偿也失败则升级 L4；
   journal 每条 {saga_id, step, intent_hash, before_hash, after_hash, ts, trace_id}
4. **高风险强制 Saga**：descriptor `risk ≥ high` 的操作必须走 Saga；`undo_hint` 必须指向真实可执行动作（S1-02 已回填）
5. **分裂脑防护（P7.2-16）**：单机仅允许一个 Watchdog（lockfile 强制）；集群化留 P5
6. **注入防御六机制接线（§5.7）**：taint 标记外来文本（禁入 system prompt/决策分支）、指令/数据分离
   （工具参数只由决策层生成）、能力最小暴露（配合 S4-04 裁剪）、出域链路监测（配合 S4-02）、
   人机边界词（转账/发布/删库/改权限/push --force → UI 显式确认 + 60s 时效）、UI 安全渲染（后端约束 + 配合 S6-01）

━━━ 三、已就绪前置：勿重复实现 ━━━
  • 自愈设施（既有）：agent/self_healing/（五层健康探针归一化 / 熔断降级统一 / 失败路径反思闭环 / 状态快照）
  • 单例与锁：agent/monitoring/lock_watchdog.py（分裂脑防护复用其 lockfile 语义）
  • 审计与事件（S2 交付）：agent/audit/facade.py::audit.record(...)；agent/observability/events.py（healing.triggered 等）
  • 审批（S4-01 交付）：approval.py 的 Actor 矩阵与审批入口（Saga 升级/人工接管走既有审批，勿自建）
  • 契约字段（S1-02 已回填）：agent/descriptors → governance.undo_hint / compensating_action（risk≥high 已带真实补偿描述）
  • 混沌演练既有规程与报告（docs/chaos_test_report_*.md、docs/chaos_engineering_guide.md）→ 复用而非新建

━━━ 四、本任务特有硬约束 ━━━
1. **整包回滚不可拆**：任何"只回技能不回代码"的路径都必须被拒绝（这类不一致直接触发 L4）
2. Saga 补偿必须**幂等可重放**；补偿失败必须升级而非静默
3. taint 内容**不得进入 system prompt 与决策分支**（要有对抗用例：注入文本试图改变工具参数）
4. 人机边界词确认绑定**单次 action + 60s 时效**，不可复用旧确认
5. 不破坏既有自愈行为：新增层级/命名为叠加，既有触发与告警阈值不回归
6. 混沌演练**至少 2 项实际跑通**并留证（建议：kill 主进程、审计链注入篡改）

━━━ 五、验收与交付物 ━━━
交付：1) `自愈语义映射表`（v7.2 L1-L5 ↔ 云枢层级）；2) ReleaseBundle 整包快照 + rollback_bundle + 部分回滚拒绝；
      3) agent/self_healing/saga.py（prepare/execute/confirm + journal + compensate）；
      4) 注入防御六机制接线（taint/指令数据分离/边界词/出域监测配合项）；
      5) 混沌演练 ≥2 项记录；6) TASK-S4-03_验收报告.md；7) S4-03_交付结案报告_<日期>.md；8) 更新 00_总览 状态行
验收：逐条对照任务书 §四（含整包回滚拒绝用例、Saga 升级 L4、taint 对抗用例、lockfile 防分裂脑、混沌 ≥2 项）

━━━ 六、本地门禁 ━━━
  • 相关套件：pytest tests/unit/test_self_healing*.py tests/unit/test_heal*.py tests/unit/test_watchdog*.py（按实际）
  • 邻接回归：pytest tests/unit/test_guardrails*.py（taint 接线）+ tests/unit/test_audit*.py + approval 邻接
  • 全量抽查：pytest -m "not slow" -p no:randomly
  • kwarg 扫描两条：--path agent --min-risk HIGH；--path tests --min-risk HIGH
  • mypy 新增/改动模块 + 既有阻塞模块；importlinter lint --config .importlinter
  • 真实提交场景验证 pre-commit；跑完门禁后 git status 检查产物漂移并还原

━━━ 七、上游已知坑 ━━━
1. 自愈/熔断类用例会改动全局状态与定时器 → 用例后必须**复位**（否则污染后续用例；既有轮次出现过）
2. 涉及快照/回滚的用例严禁操作真实仓库数据 → 用临时目录 + 显式路径；**绝不在测试里真跑 git checkout/merge**
3. 混沌演练（kill 进程）会中断本地服务 → 在受控环境执行并记录，勿在生产/主工作区直接 kill
4. 与 S4-02 边界：出域拦截的**执行点**可能在 guardrails/egress，注意勿与 S4-02 的策略引擎重复实现
5. lockfile 语义复用既有实现，勿另建第二套锁（会与 SingletonManager/lock_watchdog 冲突）

━━━ 八、回报格式 ━━━
见批次总表 §三"统一回报格式"；额外须附：混沌演练 ≥2 项的实测记录（命令 + 输出 + 恢复验证）与 L1-L5 映射表
```

---

## 三、开工自查清单

- [ ] S4-01 已结案/接口定稿；S5-03 与 S4-02 已结案（避免同文件并发）
- [ ] `--base master`、id=`s403`；worktree 内工作
- [ ] L1-L5 映射表完整（无同名不同义残留）
- [ ] 整包回滚拒绝"只回技能不回代码"（用例）
- [ ] Saga journal 字段齐 + 补偿失败升级 L4
- [ ] risk≥high 强制 Saga（无强制即拒绝执行）
- [ ] taint 对抗用例通过（注入文本无法改变参数/进 system prompt）
- [ ] 混沌演练 ≥2 项实跑并留证
- [ ] 本地门禁全绿；覆盖率 ≥80%
- [ ] 双远端同点推送
