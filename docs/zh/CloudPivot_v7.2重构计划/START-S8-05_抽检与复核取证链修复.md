# START-S8-05 抽检与复核取证链修复（分发壳 · 可直接复制）

> **本文件不是任务书。** 任务规格 → [`TASK-S8-05_抽检与复核取证链修复.md`](TASK-S8-05_抽检与复核取证链修复.md)（★ 必须先完整阅读）
> 输入依据 → [`../人工复核裁定记录_20260912.md`](../人工复核裁定记录_20260912.md)（§四 的 D1–D4 缺陷与证据）
> 批次与通用约定 → [`PARALLEL_S8批次总表.md`](PARALLEL_S8批次总表.md)｜基线：`master`｜预估：4–6 人日

---

## 一、开工前确认

1. `--base master`；worktree id 用 **`s805`**。
2. 依赖 S3-01/S3-02/S3-03/S1-02——**均已结案**；本任务的 4 个缺陷来自 **2026-09-12 人工复核实操**（不是推测）。
3. **本次 6 条已裁定结论不得改写**（4 条保持 `unknown`、2 条已写入 `internal`）——本任务是让"这些裁定被系统记住"，不是重新裁定。

---

## 二、启动提示词（整段复制给新会话）

```
【任务】TASK-S8-05 — 抽检与复核取证链修复（队列完整性 / 适用性 / 归组键 / 裁定留痕）
【任务书】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\TASK-S8-05_抽检与复核取证链修复.md（★ 必须先完整阅读）
【输入依据】C:\Users\Administrator\agent\docs\zh\人工复核裁定记录_20260912.md（§四 D1–D4：缺陷现象、证据、影响）
【批次总表】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\PARALLEL_S8批次总表.md
【预估】4–6 人日｜【状态】无待裁定项

━━━ 一、工作区与隔离（强制）━━━
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s805 --base master
此后所有 git 操作在 .worktrees/s805/ 内；主工作区禁令与提交流程见批次总表 §三。
双远端推送：git push origin master && git push gitee master

━━━ 二、四个缺陷与修复目标（详见任务书 §一）━━━
D1 判定集重生成产生孤儿引用 → 入队/读取校验 case 存活；孤儿标 stale（不删记录）；重生成联动扫描队列
D2 case↔capability 适用性未落地（3 步链被归入单能力）→ 显式适用性字段（S3-02 遗留 #5/M4）；抽检取样按形状过滤
D3 intent_key 无区分度（CJK 按字切分，7 条同键）→ 归组键加结构维度（能力集合 + 步骤数档位）；给冲突率对比
D4 人工裁定无留痕反馈（已写 internal 仍报 DC-2）→ ResolutionStore 裁定台账 + needs/队列跳过已裁定项 + 审计留痕

━━━ 三、关键认识（D4 的本质，决定修法）━━━
"改内容"能被规则识别（本次 RK-5 因正文修订而消失），"改 trust 值"不能（2 条 data_class 已写入仍被报）。
→ 修复方向：让"人工裁定"成为规则可读的一等输入，而不是靠改内容绕开。
→ 反面纪律：不得为了"清空清单"而放宽规则；已裁定的不再重复报，未裁定的仍要报。

━━━ 四、已就绪前置：勿重复实现 ━━━
  • 队列与灰度：agent/digestion/shadow.py（ManualReviewQueue / ShadowLedger / shadow_run 台账）
  • 判定集与沙箱：agent/digestion/cases.py（CaseStore/EquivalenceCase/versions/applicability）、sandbox.py
  • 清洗与归组：agent/digestion/cleaning.py（intent_key_for_trace / same_task_key / normalize_intent）
  • needs 规则：agent/descriptors/backfill.py（PRV-*/DC-*/RK-* 规则与 needs_review 文案）
  • 写入与审计：agent/descriptors/registry.py（update_trust/mark_provenance）、agent/audit/facade.py（audit.record）
  • 复核 CLI：scripts/demo_s3_03_internalize.py（--review-sheet / --record-review）、scripts/run_s1_02_backfill.py（--dry-run）
  ★ 不要自建第二套队列/判定集/台账

━━━ 五、本任务特有硬约束 ━━━
1. **不得改写已裁定结论**（本次 6 条按裁定记录登记）
2. **不得为清空而放宽规则**；只做"已裁定不重复提醒"
3. 孤儿/stale 记录**保留不删**（留痕优先），只在报告中标注
4. 裁定记录**必须入审计**（resolution.record），可追溯可验签
5. 报告须显示"已裁定项及其依据"，避免"看不清为什么消失"
6. 不引入外部依赖（分词库等）；若用受控词表须版本化并披露

━━━ 六、验收与交付物 ━━━
交付：1) 队列完整性校验（stale 标注 + 重生成联动）；2) 显式适用性判定 + 取样过滤；
      3) 归组键改进 + 冲突率对比；4) ResolutionStore + needs/队列接入 + 审计联动；
      5) 本次 6 条登记并验证 needs_review 清空；6) 端到端"入队 → 裁定 → 重算"闭环演示；
      7) TASK-S8-05_验收报告.md；8) S8-05_交付结案报告_<日期>.md；9) 更新 00_总览 状态行
验收：逐条对照任务书 §四（含 stale 不删、取样形状过滤、冲突率对比、清单清空且依据可见）

━━━ 七、本地门禁 ━━━
  • 相关套件：pytest tests/unit/test_digestion_*.py tests/unit/test_descriptors*.py（新增 + 邻接）
  • kwarg 扫描两条（--path agent 与 --path tests）；mypy 新增/改动模块；importlinter
  • 真实提交场景验证 pre-commit；跑完门禁后 git status 检查产物漂移并还原
  • 涉及 data/digestion 与 data/descriptors* 的用例必须**显式传路径或 autouse 隔离**（S3-02/S3-03 两次污染教训）

━━━ 八、上游已知坑 ━━━
1. 判定集有 5 个版本且会重生成 → 用例里不要硬编码版本号或 case_id
2. `intent_key` 改进会影响既有"同类轨迹"用例断言 → 需同步更新并**如实说明口径变更**（属口径变更，不是作弊）
3. needs 重算有 dry-run 双跑一致性校验（deterministic）→ 改动后须仍保持一致
4. 复核 CLI 的 `--verdict` **默认值是 pass**（危险）→ 测试与文档中务必显式传值
5. 时间/追加写台账用注入时钟与显式路径，避免跨日与污染

━━━ 九、回报格式 ━━━
见批次总表 §三"统一回报格式"；额外须附：D1–D4 逐条修复证据（含单测与端到端演示）、
改进前后同键冲突率对比、"清单清空且依据可见"的证据（含审计记录）
```

---

## 三、开工自查清单

- [ ] 已读任务书与裁定记录 §四（D1–D4 证据）
- [ ] `--base master`、id=`s805`；worktree 内工作
- [ ] 未改写已裁定结论；未放宽规则
- [ ] 孤儿记录标 `stale` 而非删除
- [ ] 取样按形状过滤（对 read_file 重采样验证）
- [ ] 归组键含结构维度 + 冲突率对比
- [ ] 裁定记录入审计；报告可见依据
- [ ] 登记本次 6 条后 needs_review = 0（端到端演示）
- [ ] 双远端同点推送
