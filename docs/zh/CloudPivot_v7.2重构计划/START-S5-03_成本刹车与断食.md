# START-S5-03 成本刹车与断食（分发壳 · 可直接复制）

> **本文件不是任务书。** 任务规格 → [`TASK-S5-03_成本刹车与断食.md`](TASK-S5-03_成本刹车与断食.md)（★ 执行会话必须完整阅读）
> 批次与通用约定 → [`PARALLEL_并行铺开总表.md`](PARALLEL_并行铺开总表.md)
> 基线：`master` / `15eae00d`｜波次：**第一波**｜预估：3–5 人日
> ⚠ 与 S4-03 在 `agent/monitoring/` 有落点重叠 → **建议本任务在第一波先结案**，S4-03 排第二波（总表已如此安排）。

---

## 一、开工前确认

1. `--base master`；worktree id 用 **`s503`**。
2. 依赖 S2-03（成本事件/ACR）已结案；**两项开工前裁定已由 Owner 关闭（2026-09-11）**：
   - **C 归一化系数 → 沿用价格锚定系数表，本次不校准**；触发条件：**L2 Core-50 基线就绪后**（由 S5-02 产出）启动校准评估（季度复校）；本任务只需在文档与指标输出中**标注口径版本**。
   - **D 双成本轨 → 收敛到事件流单一数据源**：`utc.record_cost()` → `data/events/` 为唯一来源；旧 `cost_log.jsonl` **停写 + 只读兼容 ≤1 minor + 归档不删**；`reconcile_cost_log()` 降级为对账工具或下线；若发现仍有生产调用方，**先补报警再收敛**（不得静默丢账）。

---

## 二、启动提示词（整段复制给新会话）

```
【任务】TASK-S5-03 — UTC 日/周双层刹车 + 断食 + 性能预算重定（对齐 v7.2 §7 / §6.3 / P7.2-06）
【任务书】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\TASK-S5-03_成本刹车与断食.md（★ 必须先完整阅读，尤其步骤 1 的两项已定裁定 C/D）
【批次总表】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\PARALLEL_并行铺开总表.md
【所属计划】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\00_总览_审计结论与重构总计划.md
【上游设计文档】C:\Users\Administrator\Desktop\设计思路\CloudPivot_v7.2_final_合并归档(智谱审核版).md
                （相关章节：§7 断食规则 / P7.2-06 双层成本刹车 / §6.3 θ 分阶段 / §11.2 性能预算）
【预估】3–5 人日
【状态】Owner 裁定 C/D 已关闭；依赖 S2-03 已结案

━━━ 一、工作区与隔离（强制）━━━
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s503 --base master
此后所有 git 操作在 .worktrees/s503/ 内；主工作区禁令与提交流程见批次总表 §三。
双远端推送：git push origin master && git push gitee master
⚠ 落点 agent/monitoring/ 与 S4-03 重叠：本任务在第一波，S4-03 在第二波（待 S4-01），天然错开

━━━ 二、任务目标（摘要）━━━
1. **日级硬熔断**（P7.2-06）：当日成本 > budget.daily_cents → 立即停**非关键** outbound（关键＝用户显式请求 / 审批中任务），
   次日 00:00 自动恢复；触发记审计 + 事件
2. **周级断食**：进（日均 UTC > 1.3×）→ 降本模式（限制非关键 LLM/消化/影子任务）→ 出（连续 24h ≤ 1.1×）→ 冷却 12h
3. **θ 分阶段阈值（§6.3）**：W1-W4 不设 → W5-W9 ≤1.5×→1.3× → W10-W14 ≤1.0× → M4+ ≤0.8×→0.6× → M7+ ≤0.5×（配置驱动，无硬编码）
4. **性能预算重定**：以云枢**实测基线**替换 §11.2 假设值，产出 PERF_BUDGET_REBASED.md（引用既有压测报告）
5. **审批衰减率**（§6.7）：策略自动消化占比可计算（披露不考核）

━━━ 三、已就绪前置：勿重复实现 ━━━
  • 成本写入与聚合（S2-03 交付）：agent/observability/utc.py → record_cost() / utc_daily() / utc_weekly() /
    utc_window() / coefficient_table() / anchor_prices_cents() / resolve_anchor_model()
  • ACR 与审批事件：agent/observability/acr.py（acr_daily / acr_weekly / acr_snapshot / record_approval）
  • 既有成本监控脚本（数据源参考）：verify_budget_break.py 等
  • **S3-03 已交付（同源设施）**：agent/digestion/internalize.py::ROIReport 消费的正是 utc.utc_window()（口径天然一致）；
    agent/digestion/shadow.py → ShadowReport.overhead.**shadow_overhead_ms**（灰度自身开销，**须计入 UTC 口径**）
  • 断食联动点：S3-03 的灰度/重探开关默认关闭（CP_DIGESTION_* / CP_EVENTS_* 等），断食期可直接作用于这些开关
  ★ 勿另建成本聚合或第二套预算记账

━━━ 四、本任务特有硬约束 ━━━
1. 熔断只停**非关键** outbound：用户显式请求与审批中任务不得被熔断（用例覆盖）
2. 所有阈值走 `.env`/config（`CP_BUDGET_DAILY_CENTS`、`CP_BUDGET_WEEKLY_UTC_HIGH` 等），非法值回退默认，**默认不误伤**
3. 断食状态机需可观测（状态、进入/退出时间、当前阈值来源）+ 审计留痕
4. 成本口径文档必须标注「当前＝价格锚定系数，待 L2 基线后校准」+ 口径版本号
5. 双成本轨收敛要**可回滚**（保留只读兼容 ≤1 minor），不得让历史数据无法查询

━━━ 五、验收与交付物 ━━━
交付：1) 日级硬熔断 + 周级断食（配置化 + 审计/事件）；2) θ 分阶段阈值表；3) PERF_BUDGET_REBASED.md；
      4) 审批衰减率度量（披露）；5) 双成本轨收敛落地（停写 + 兼容读 + 文档标注）；
      6) TASK-S5-03_验收报告.md；7) S5-03_交付结案报告_<日期>.md；8) 更新 00_总览 状态行
验收：逐条对照任务书 §四（含裁定 C 落文档、裁定 D 收敛、ROI 同源、shadow_overhead 计入口径）

━━━ 六、本地门禁 ━━━
  • 相关套件：pytest tests/unit/test_cost*.py tests/unit/test_budget*.py tests/unit/test_utc*.py（按实际）
  • 邻接回归：pytest tests/unit/test_acr*.py + monitoring 邻接 + digestion 邻接（ROI 同源）
  • 全量抽查：pytest -m "not slow" -p no:randomly
  • kwarg 扫描两条：--path agent --min-risk HIGH；--path tests --min-risk HIGH
  • mypy 新增/改动模块 + 既有阻塞模块；importlinter lint --config .importlinter
  • 真实提交场景验证 pre-commit；跑完门禁后 git status 检查产物漂移（含 data/cost_daily.json）并还原

━━━ 七、上游已知坑 ━━━
1. 成本类用例若落盘必须**显式传路径或 autouse 隔离**（S3-02/S3-03 两次运行时区污染教训）
2. 时间相关断言（跨日 00:00 恢复、24h 退出窗口、冷却 12h）**注入时钟或相对时间**，勿依赖真实墙钟
3. 断食/熔断会改动全局行为，用例必须验证"未超阈值时零影响"（防误伤）
4. `data/cost_daily.json` 等运行时产物会被门禁脚本或测试写入 → 记得还原，避免误提交
5. 生产 waitress 单进程假设下的并发写：沿用既有单写者 + busy_timeout 兜底，不自建锁

━━━ 八、回报格式 ━━━
见批次总表 §三"统一回报格式"；额外须附：熔断/断食**演练记录**（触发→恢复实测输出）与裁定 C/D 的落地证据
```

---

## 三、开工自查清单

- [ ] 已读任务书，确认裁定 C（不校准 + 标注口径）与 D（收敛事件流）的落地方式
- [ ] `--base master`、id=`s503`；worktree 内工作
- [ ] 日熔断只停非关键 outbound（用户请求/审批中任务不受影响）
- [ ] 断食进/出/冷却三态用例 + 实测演练
- [ ] shadow_overhead_ms 已计入 UTC 口径
- [ ] 旧 cost_log.jsonl 停写且只读兼容；无静默丢账
- [ ] 时间类用例使用注入时钟
- [ ] 本地门禁全绿；运行时产物已还原
- [ ] 双远端同点推送
