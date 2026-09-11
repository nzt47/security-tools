# START-S4-02 策略即代码（分发壳 · 可直接复制）

> **本文件不是任务书。** 任务规格 → [`TASK-S4-02_策略即代码.md`](TASK-S4-02_策略即代码.md)（★ 执行会话必须完整阅读）
> 批次与通用约定 → [`PARALLEL_并行铺开总表.md`](PARALLEL_并行铺开总表.md)
> 基线：`master` / `15eae00d`｜波次：**第一波**（无依赖摩擦）｜预估：5–8 人日
> ⚠ 落点 `agent/guardrails/` 与 S4-03（第二波）重叠 → 波次已错开；本任务对 guardrails 的改动请集中、可读。

---

## 一、开工前确认

1. `--base master`；worktree id 用 **`s402`**。
2. 依赖 S2-02（审计）/S2-03（policy.denied 事件）已结案；**无待裁定项**。
3. **架构修正（审计 P4）已在任务书中固化**：单机不强制引入 OPA/WASM，采用**等效声明式策略引擎**——但**接口形状须兼容未来替换 OPA**（`PolicyEngine.check(policy_ctx) -> {allow|deny|ask, policy_id}`）。

---

## 二、启动提示词（整段复制给新会话）

```
【任务】TASK-S4-02 — 策略即代码（等效声明式引擎 + 策略模拟器 + 决策/执行分离）
【任务书】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\TASK-S4-02_策略即代码.md（★ 必须先完整阅读）
【批次总表】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\PARALLEL_并行铺开总表.md
【所属计划】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\00_总览_审计结论与重构总计划.md
【上游设计文档】C:\Users\Administrator\Desktop\设计思路\CloudPivot_v7.2_final_合并归档(智谱审核版).md
                （相关章节：§5.6 策略即代码 + 决策缓存 p99<5ms / P7.1-20 OPA 只出决策不做网络动作 / P7.2-19 策略模拟器 / §3.11 Policy）
【预估】5–8 人日
【状态】依赖 S2-02/S2-03 已结案；无待裁定项

━━━ 一、工作区与隔离（强制）━━━
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s402 --base master
此后所有 git 操作在 .worktrees/s402/ 内；主工作区禁令与提交流程见批次总表 §三。
双远端推送：git push origin master && git push gitee master
⚠ guardrails/ 与 S4-03（第二波）重叠：本任务在第一波先落地；改动集中、命名清晰，便于后续合并

━━━ 二、任务目标（摘要）━━━
1. **Policy schema（§3.11）**：{id, version, owner, effect: allow|deny|ask, match, message_template,
   effective_range, break_glass_ttl_min, signature}；版本化 + 生效范围 + 签名校验
2. **决策引擎**：PolicyEngine.check(policy_ctx) → {allow|deny|ask, policy_id}；决策缓存 **p99 < 5ms**；
   决策入链式审计（S2-02）+ policy.decision 埋点（S2-03）
3. **职责边界（P7.1-20，关键）**：策略引擎**只出决策、不做网络动作**；数据出域由 **egress guard（执行点）** 强制。
   引擎不得提供 `http.send` 类能力；LLM 也不得生成网络副作用
4. **match 子集**：实现 OPA 子集的等效判定（字段路径比较 / 集合成员 / 布尔组合），并**文档化支持与不支持的语法**
5. **收件箱只收例外**：未被策略覆盖或需 break-glass 的例外进人工收件箱（复用 hitl/takeover_queue）
6. **策略模拟器（P7.2-19）**：对历史 PolicyDecision 重放候选新策略 → 输出 deny→allow 变更数 + 高危命中清单；
   **策略变更 PR 必附模拟报告**（合入门禁）

━━━ 三、已就绪前置：勿重复实现 ━━━
  • 审计与事件（S2 交付）：agent/audit/facade.py::audit.record(...)；agent/observability/events.py（emit / policy.denied 等类型）
  • 既有权限/护栏：agent/guardrails/（input_guard / output_guard / output_schema）、RBAC 与权限路由（见 docs/permission_arch.md）、
    agent/secure_config 配置设施
  • 人工收件箱：agent/human_in_the_loop/（takeover_queue / hitl）
  • 契约字段（S1 交付）：agent/descriptors（descriptor.trust.data_class / risk_level / origin.external_endpoint）
    → 出域判定直接读这些字段，勿另建分级
  ★ 新建 agent/policy/ 包承载策略引擎；勿把策略逻辑散落到各调用点

━━━ 四、本任务特有硬约束 ━━━
1. **决策与执行分离**：`check()` 返回决策即止；任何真实网络/文件动作都不在引擎内（含"看似便利"的快捷分支）
2. `data_class=secret` 且目标外部 → deny（决策层），并由执行点强制拦截（两层都要有用例）
3. 决策缓存必须可失效（策略变更即失效），且缓存 p99<5ms 需有实测证据
4. 策略变更走「模拟报告 + 人工合入」，不得运行时静默改策略
5. 不得破坏既有权限行为：策略未覆盖的情形**回退既有判定**（不得因新引擎导致原本允许的操作被拒）

━━━ 五、验收与交付物 ━━━
交付：1) agent/policy/（Policy/PolicyStore/PolicyEngine + match 子集文档）；2) 决策缓存 + 审计/事件埋点；
      3) 策略模拟器 + 变更 PR 合入门禁；4) 至少一个真实执行点的"决策-执行"分离接线 + secret 出域拒绝链路；
      5) TASK-S4-02_验收报告.md（含模拟器样例报告）；6) S4-02_交付结案报告_<日期>.md；7) 更新 00_总览 状态行
验收：逐条对照任务书 §四（schema 对齐 / 缓存 p99 实测 / secret 出域双层拦截 / 无网络副作用 / 模拟器 + PR 门禁）

━━━ 六、本地门禁 ━━━
  • 相关套件：pytest tests/unit/test_policy*.py tests/unit/test_guardrails*.py tests/unit/test_permission*.py（按实际）
  • 邻接回归：pytest tests/unit/test_audit*.py + tests/unit/test_events*.py + skills/approval 邻接
  • 全量抽查：pytest -m "not slow" -p no:randomly
  • kwarg 扫描两条：--path agent --min-risk HIGH；--path tests --min-risk HIGH
  • mypy 新增模块 + 既有阻塞模块；importlinter lint --config .importlinter（新包注意依赖方向，勿反向依赖上层）
  • 真实提交场景验证 pre-commit；跑完门禁后 git status 检查产物漂移并还原

━━━ 七、上游已知坑 ━━━
1. 新包引入后**架构护栏**（arch_rules / importlinter）易报循环依赖 → 用依赖倒置（接口在 policy，实现在调用方注册）
2. 性能断言（p99<5ms）在 CI 覆盖率插桩下会抖动 → 用相对断言或标注 clock 口径，并对缓存命中路径单独度量
3. 策略类用例若落盘（策略库/决策缓存）必须**显式传路径或 autouse 隔离**
4. 与审批（S4-01）的边界：策略 `ask` 与审批流是两个概念——`ask` 应路由到审批/收件箱，勿在策略引擎内直接审批
5. 勿在日志/审计里写敏感匹配值（沿用既有脱敏口径）

━━━ 八、回报格式 ━━━
见批次总表 §三"统一回报格式"；额外须附：模拟器样例报告（deny→allow 变更数 + 高危清单）与缓存 p99 实测输出
```

---

## 三、开工自查清单

- [ ] 已读任务书（含 P4 修正：等效引擎 + 兼容 OPA 的接口形状）
- [ ] `--base master`、id=`s402`；worktree 内工作
- [ ] 引擎纯决策、零网络副作用（含测试断言）
- [ ] secret 出域在决策层与执行点**双层**拦截
- [ ] 决策缓存 p99 实测且有失效机制
- [ ] match 子集支持/不支持语法已文档化
- [ ] 策略未覆盖时回退既有权限行为（零行为回归用例）
- [ ] 模拟器 + PR 合入门禁可用
- [ ] 本地门禁全绿（含架构护栏）；覆盖率 ≥80%
- [ ] 双远端同点推送
