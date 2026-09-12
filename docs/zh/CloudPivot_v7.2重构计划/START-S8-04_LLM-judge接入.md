# START-S8-04 LLM-judge 接入（分发壳 · 可直接复制）

> **本文件不是任务书。** 任务规格 → [`TASK-S8-04_LLM-judge接入.md`](TASK-S8-04_LLM-judge接入.md)（★ 必须先完整阅读）
> 批次与通用约定 → [`PARALLEL_S8批次总表.md`](PARALLEL_S8批次总表.md)｜基线：`master` / `7e094eab`｜预估：3–4 人日

---

## 一、开工前确认

1. `--base master`；worktree id 用 **`s804`**。
2. 依赖 S3-02（三层比对/注入通道）、S3-03（灰度 judge）、S5-03（UTC 口径）、S2-03（成本事件）——**均已结案**。
3. **需要 Owner 提供凭证配置**（`CP_DIGESTION_JUDGE_PROVIDER` / `CP_DIGESTION_JUDGE_MODEL`）——**若没有，交付"具备即用"能力并在报告中如实写"未在真实凭证下验证"**，不得编造。

---

## 二、启动提示词（整段复制给新会话）

```
【任务】TASK-S8-04 — LLM-judge 凭证接入与成本护栏
【任务书】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\TASK-S8-04_LLM-judge接入.md（★ 必须先完整阅读）
【批次总表】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\PARALLEL_S8批次总表.md
【上游依据】S7-05 未完成项 #1（judge_kind = deterministic_local(llm_unavailable)，21/21 次灰度未跑真实 judge）；
            S3-02 遗留 M1；S3-03 灰度期 judge 注入通道；v7.2 §4.5 三层比对（层③ LLM-judge ≥0.85）
【预估】3–4 人日｜【状态】无待裁定项；需 Owner 提供凭证（可选）

━━━ 一、工作区与隔离（强制）━━━
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s804 --base master
此后所有 git 操作在 .worktrees/s804/ 内；主工作区禁令与提交流程见批次总表 §三。
双远端推送：git push origin master && git push gitee master

━━━ 二、任务目标（摘要）━━━
1. 配置与凭证自检：provider / model / daily_budget_cents / enabled（**默认 false**）；
   凭证优先 SecretStore、回落 .env；启动自检输出三态 available / no_credentials / disabled（面板与日志可读）；
   日志/审计/事件中**不得出现密钥明文**
2. 真实 judge：在既有注入通道实现 LLMJudge，输出结构化 {verdict, confidence, reason}；
   **confidence ≥ 0.85 视为软性通过**；解析失败按 E_UPSTREAM_FORMAT 语义处理（不猜）
3. **judge_kind 如实标注**（诚信底线）：真实 → llm:<provider>:<model>；回落 → deterministic_local(<具体原因>)
4. **成本护栏（关键）**：
   · 每次 judge 调用 utc.record_cost(...) **计入 UTC**（标注来源为 judge）
   · **每日 judge 预算**超限 → 自动停用真实 judge + 回落 + 标注原因 + 发事件（不静默）
   · 与 S5-03 日熔断/周断食联动（断食期可自动停用，策略可配，默认跟随）
   · 报告**两栏分离**：业务成本 / judge 成本（不得混算，沿用 S5-02 两列纪律）
5. 抽检联动：10% 人工抽检队列与 judge 结果并列存档 → 输出**一致率统计** + 分歧样本清单；
   分歧样本入 ManualReviewQueue；样本 <20 只披露不结论
6. 层③ 定位：judge **仅用于软性第三层**，不得替代前两层（结构 schema / 副作用集合）硬性比对

━━━ 三、已就绪前置：勿重复实现 ━━━
  • 三层比对与注入通道：agent/digestion/sandbox.py（ReplaySandbox 的 judge= 注入 + judge_kind 记录）
  • 灰度 judge 使用点：agent/digestion/shadow.py（judge_kind 三态如实标注已具备）
  • 成本写入与聚合：agent/observability/utc.py（record_cost / utc_daily / utc_window / calibration 块）
  • 成本事件：agent/observability/events.py（cost 事件）
  • 人工复核队列：agent/digestion/internalize.py（ManualReviewQueue）
  • 脱敏：agent/guardrails/（凭据脱敏口径）+ agent/audit/facade.py 的 redact
  ★ 不要自建第二套成本聚合或 judge 框架

━━━ 四、本任务特有硬约束 ━━━
1. **默认关闭**：真实 judge 有额外成本，enabled 默认 false；开启后仍受每日预算约束
2. **不得冒充**：无凭证时 judge_kind 必须是 deterministic_local(no_credentials)，不得写 llm
3. judge 成本**必须计入 UTC**（用例断言成本可查、两栏不混算）
4. 超预算自动回落 + 事件；解析失败不静默
5. 报告与日志无密钥明文
6. 无凭证环境交付"具备即用"，**报告须写明"未在真实凭证下验证"**（不编造）

━━━ 五、验收与交付物 ━━━
交付：1) judge 配置项 + 凭证自检（三态）；2) LLMJudge（结构化 + 0.85 阈值 + 解析失败处理）；
      3) 成本护栏（计入 UTC + 每日预算 + 超限回落 + 事件）；4) 两栏成本口径 + 一致率统计 + 分歧入队；
      5) TASK-S8-04_验收报告.md；6) S8-04_交付结案报告_<日期>.md；7) 更新 00_总览 状态行
验收：逐条对照任务书 §四（含 0.85 边界、judge_kind 如实、成本计入、两栏不混算、无明文）

━━━ 六、本地门禁 ━━━
  • 相关套件：pytest tests/unit/test_judge*.py（新增）+ digestion（sandbox/gate/shadow）+ observability（utc）+ eval 邻接回归
  • kwarg 扫描两条（--path agent 与 --path tests）；mypy 新增模块；importlinter
  • 真实提交场景验证 pre-commit；跑完门禁后 git status 检查产物漂移并还原

━━━ 七、上游已知坑 ━━━
1. 无凭证环境 → 用**桩 judge** 测通道/阈值/成本/回落；真实路径 gate 并如实标注
2. judge 调用会产生真实费用 → 小样本 + 预算上限；**优先短提示与廉价模型**
3. 阈值边界用例（0.84 / 0.85 / 0.86）要精确构造（浮点比较用容差）
4. 成本类用例落盘要**显式传路径或 autouse 隔离**（S3-02/S3-03 教训）
5. 改 judge 相关字段会影响 S3-03 既有断言与 S6-01 面板数据结构 → 兼容叠加、勿改既有字段语义
6. 脱敏：断言失败时不要把密钥/提示词原文打进日志

━━━ 八、回报格式 ━━━
见批次总表 §三"统一回报格式"；额外须附：judge 可用性三态证据、成本两栏对照、一致率统计（若有人工样本）、
"是否在真实凭证下验证"的明确声明
```

---

## 三、开工自查清单

- [ ] 已读任务书；确认凭证可用性与"默认关闭"要求
- [ ] `--base master`、id=`s804`；worktree 内工作
- [ ] 三态自检可读；无凭证时不冒充 llm
- [ ] 0.85 阈值边界用例（0.84/0.85/0.86）
- [ ] judge 成本计入 UTC 且两栏不混算（断言）
- [ ] 超预算自动回落 + 事件
- [ ] 日志/审计/事件无密钥明文
- [ ] 报告明确声明是否在真实凭证下验证
- [ ] 双远端同点推送
