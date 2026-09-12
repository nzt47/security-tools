# START-S7-03 成本系数校准（分发壳 · 可直接复制）

> **本文件不是任务书。** 任务规格 → [`TASK-S7-03_成本系数校准.md`](TASK-S7-03_成本系数校准.md)（★ 必须先完整阅读）
> 批次与通用约定 → [`PARALLEL_S7批次总表.md`](PARALLEL_S7批次总表.md)｜基线：`master` / `531515e0`｜预估：3–5 人日

---

## 一、开工前确认

1. `--base master`；worktree id 用 **`s703`**。
2. **触发条件已满足**：S5-02 已交付 **L2 Core-50**，S5-03 的 `calibrated=False`（`price_anchor.v1`）正等待校准。
3. **凭证前置检查**：确认可用模型凭证（`LLM_API_KEY` / `DEEPSEEK_API_KEY` 等）。**无多模型凭证时走降级路径并如实标注**，不得编造。

---

## 二、启动提示词（整段复制给新会话）

```
【任务】TASK-S7-03 — 成本系数校准：把"价格锚定"升级为"实测校准"（或如实报告未完成）
【任务书】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\TASK-S7-03_成本系数校准.md（★ 必须先完整阅读）
【批次总表】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\PARALLEL_S7批次总表.md
【上游设计文档】C:\Users\Administrator\Desktop\设计思路\CloudPivot_v7.2_final_合并归档(智谱审核版).md
                （相关章节：§6.2 UTC 刹车 / §6.6 成本埋点 / §6.7 指标字典 / §6.8 模型矩阵）
【预估】3–5 人日
【状态】L2 Core-50 已交付（触发条件满足）；裁定 C 的"待 L2 后校准"在此执行

━━━ 一、工作区与隔离（强制）━━━
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s703 --base master
此后所有 git 操作在 .worktrees/s703/ 内；主工作区禁令与提交流程见批次总表 §三。
双远端推送：git push origin master && git push gitee master

━━━ 二、任务目标（摘要）━━━
把成本口径从"按标价换算"升级为"按实测换算"，让 UTC 断食 / ROI / 审批衰减率建立在真实成本比上。
1. 校准方案先行：样本 = L2 Core-50（212 判定条目）；变量 = 锚模型 + 2–4 个参与模型；
   指标 = token(in/out)/单位任务 token/重试/成功率/正常化成本；对照 = 与 price_anchor 系数逐项比偏差；复校周期建议季度
2. 执行：脚本 scripts/calibrate_cost_coefficients.py
   · 路径 A（有凭证）：对 L2 Core-50 在每个参与模型上实跑，采集 utc.record_cost() 事件 + TraceStore cost 字段
   · 路径 B（无凭证，降级）：离线重放 data/events/ 历史 cost 事件（按模型分组）+ 允许导入外部实测 CSV；
     **报告中必须显式写明"未完成完整实测校准，置信度受限"**
3. 系数计算：measured_coef(model) = 单位任务正常化成本(model) / 单位任务正常化成本(anchor)；
   输出偏差表（模型 / 价格系数 / 实测系数 / 偏差率 / 样本量 / 置信度）；**实测显著偏离标价的模型要点名**
4. 落地（版本化，不追溯历史）：
   · utc.py::coefficient_table() 支持 source ∈ {price_anchor, measured} 与优先级（有实测用实测，否则回落）
   · calibration 块升级：calibrated=True + version=measured.v1 + 生效模型清单 + 校准日期
   · **历史数据不追溯**（旧记录保留原口径版本，避免历史成本数字被改写）
5. 文档：更新 PERF_BUDGET_REBASED.md / 成本口径说明，明确"哪些模型已实测、哪些仍是价格锚定"

━━━ 三、已就绪前置：勿重复实现 ━━━
  • 成本写入与聚合：agent/observability/utc.py（record_cost / utc_daily / utc_window / utc_weekly /
    coefficient_table / anchor_prices_cents / resolve_anchor_model）
  • 评测样本：agent/eval/ + eval/（**L2 Core-50**）+ run_eval.py
  • 成本事件：agent/observability/events.py（cost 事件）+ data/events/
  • ROI 消费方：agent/digestion/internalize.py::ROIReport（与本任务同源，改了系数它自动受益）
  ★ 不要另建成本聚合或第二套系数表

━━━ 四、本任务特有硬约束 ━━━
1. **不得编造任何数字**：每个数字可溯源到事件流或导入的 CSV；样本 <20 → 按 S5-02 口径**只披露不结论**
2. 无凭证时必须走降级路径并**显式声明"非完整实测"**（不得含糊、不得用价格系数冒充实测）
3. **历史口径不追溯**（改系数不得让历史成本数字变化）
4. 本地模型的"货币成本"应折算机时/能耗口径并显式说明，不得与 API 计价混算
5. 若结论是"实测与标价偏差可忽略"——**这本身是有效结论**，如实记录即可，不必强行改系数

━━━ 五、验收与交付物 ━━━
交付：1) docs/zh/成本系数校准方案.md；2) scripts/calibrate_cost_coefficients.py（路径 A/B）；
      3) 偏差分析报告；4) utc.py 来源标注 + calibration 版本；5) TASK-S7-03_验收报告.md；
      6) S7-03_交付结案报告_<日期>.md；7) 更新 00_总览 状态行
验收：逐条对照任务书 §四（含降级声明、样本不足只披露、不追溯历史、数字可溯源）

━━━ 六、本地门禁 ━━━
  • 相关套件：pytest tests/unit/test_utc*.py（新增系数用例）、test_acr_metrics.py、test_s5_03_cost_brake.py
  • kwarg 扫描两条（--path agent 与 --path tests）；mypy 改动模块；importlinter
  • 真实提交场景验证 pre-commit；跑完门禁后 git status 检查产物漂移（含 data/cost_daily.json）并还原

━━━ 七、上游已知坑 ━━━
1. 时间/窗口类断言用**注入时钟或相对时间**（跨日 00:00、24h 窗口在既有轮次踩过）
2. 成本类用例若落盘必须**显式传路径或 autouse 隔离**
3. 真实调用大模型会**产生真实费用**：控制样本量、设预算上限、优先小模型与短提示
4. 改动 `coefficient_table()` 返回值会影响 S5-03 的断食判定 → 必须验证"未校准模型回落价格系数"不产生行为回归
5. 门禁脚本/测试可能写 `data/cost_daily.json` → 记得还原

━━━ 八、回报格式 ━━━
见批次总表 §三"统一回报格式"；额外须附：偏差表 + 置信度声明 + 每个数字的来源（事件流路径或 CSV）
```

---

## 三、开工自查清单

- [ ] 已读任务书；确认凭证情况并选定路径 A/B
- [ ] `--base master`、id=`s703`；worktree 内工作
- [ ] 方案先行（样本/变量/指标/对照/复校周期）
- [ ] 无凭证时降级并显式声明"非完整实测"
- [ ] 样本 <20 只披露不结论；无编造数字
- [ ] 来源标注与优先级生效；历史口径不追溯
- [ ] 真实调用设预算上限，避免刷爆成本
- [ ] 邻接套件零回归；双远端同点推送
