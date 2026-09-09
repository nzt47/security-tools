# TASK-S3-03 shadow 灰度 + 双跑 diff + 内化触发六条件引擎（含低流量手动通道）

> 所属阶段：S3 消化流水线｜依赖：S3-01、S3-02、S2-03（成本/事件）｜预估：6–10 人日
> 来源设计文档：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §4.5（Shadow 抽样/灰度 5%/转正/签名）/§4.5.1（内化触发六条件★）/T2 修正（低流量替代通道）
> 验收报告归档于本目录 `docs/zh/CloudPivot_v7.2重构计划/`

---

## 一、目标描述

落地消化状态机的**后半程自动化**：mirrored→shadow→internalized 的灰度运行与内化决策，并解决 T2 修正（个人负载下六条件不可达 → 增加手动 promote 通道）：

1. **shadow 灰度运行**（§4.5）：每日预算 = min(日均×15%, 50 次)，trace_id 哈希确定性抽样；shadow 结果只记录不接管真实执行；副作用 record-and-replay（S3-02 沙箱）。
2. **非确定性三层比对**（复用 S3-02 验收门设施）：结构 schema 硬性 → 副作用集合硬性 → LLM-judge ≥0.85 软性 + 10% 人工。
3. **内化触发六条件引擎（P7.2-01）**：① digest_count ≥50 ② 月 samples ≥200 ③ ROI 为正（月省 > 自研一次性投入/12）④ 成功率 ≥ 上游×0.98 ⑤ p99 ≤ 上游 ⑥ privacy_gate=pass；**⑤⑥一票否决，①-④仅排序**；条件齐 → 自动创建 stage.promote PR，人工合入。
4. **T2 修正——低流量手动 promote 通道**：月 samples<200 的能力支持"手动 promote + 人工评审"路径（进 NEEDS_REVIEW 类似队列，人工确认后按④⑤⑥复核即放行），避免消化空转。
5. **ROI 报告自动附于 PR**（§4.5.1）——消费 S2-03 成本归一数据。

## 二、执行步骤

### 步骤 1：shadow 灰度器
- 新增 `agent/digestion/shadow.py`：`ShadowRunner`（每日预算 min(日均×15%,50)；trace_id 哈希确定性抽样——复用 `tracing_sampling.py` 思路）；shadow 候选执行于沙箱（S3-02），记录 shadow_overhead 成本（S2-03 字段）。
- 灰度 5%：真实流量中 5% 走候选实现（可配置开关，默认关闭——安全底线，显式阈值才开），其余 shadow 只读观察。

### 步骤 2：三层比对流水线
- `compare(upstream_result, candidate_result) -> CompareVerdict`：结构 schema（硬性）→ 副作用集合（硬性）→ LLM-judge ≥0.85（软性）+ 10% 人工抽检（硬性复核抽中者）。任一层失败 → 样本记为负例（供 R4 学错能力检测：shadow 期持续劣化 → 降级/回退）。
- 结果入 trace（capability 级 quality 统计更新：success_rate/p99/sample_count——descriptor.quality 字段，联动 S1-01）。

### 步骤 3：内化触发六条件引擎
- 新增 `agent/digestion/internalize.py`：`InternalizeEngine.evaluate(capability_id) -> {conditions, roi_report, verdict, blocker}`：
  - 数据源：digest_count（S3-01 管道计数）、月 samples（S2-01 台账 + S3-03 灰度）、ROI（S2-03 成本归一：上游月成本 - 自研月成本 - 摊销）、成功率/p99（descriptor.quality + shadow 比对）、privacy_gate（descriptor trust/data_class + 出域校验，secret/confidential 出域即拒）。
  - 规则：⑤⑥一票否决；①-④ 排序打分；全过 → `create_promote_pr()`（自动生成 PR 文本：变更 diff + ROI 报告 + 证据链接，人工合入门——复用 `git_sync.py`/谱系提交设施）。
- 调度：每日评估（复用 evolution_scheduler/task_scheduler，默认关闭或显式阈值——安全底线）。

### 步骤 4：低流量手动 promote 通道（T2）
- `manual_promote(capability_id, actor)`：人工发起 → 需满足④⑤⑥（质量/性能/隐私）复核 + 人工确认（approval 流程）→ 放行 internalized；样本不足（①-③不满足）不阻塞但显著标注"低样本人工裁定"。
- 与 S1-02 NEEDS_REVIEW 队列复用同一人工复核通道（消费 7 条/6 资产遗留——其中涉及 trust 的复核完成后可进入本通道）。

### 步骤 5：回归与归档
- 回归：digestion/descriptors/approval/审计套件零回归；新增单测（抽样确定性、预算上限、三层比对、六条件打分、⑤⑥否决、PR 生成、手动通道、负例降级）≥50 例、覆盖率 ≥80%。
- 撰写 `TASK-S3-03_验收报告.md`（含一个模拟能力的完整内化决策样例 + ROI 报告样例）。

## 三、预期成果

1. `agent/digestion/shadow.py`（ShadowRunner + 抽样 + 预算）。
2. 三层比对 CompareVerdict 流水线（含 10% 人工抽检）。
3. `agent/digestion/internalize.py`（六条件引擎 + stage.promote PR + ROI 报告）。
4. 手动 promote 通道（低流量）。
5. `TASK-S3-03_验收报告.md`。

## 四、评估标准（验收清单）

- [ ] shadow 抽样 trace_id 哈希确定性（同批两次一致）；日预算 min(日均×15%,50) 不超
- [ ] 灰度 5% 开关默认关闭；开启需显式阈值（安全底线用例）
- [ ] 三层比对任一层失败即负例；负例触发劣化降级路径（R4 防护）
- [ ] 六条件打分正确：⑤⑥ 任一不满足 → 一票否决且 blocker 明确；全过 → 自动生成 promote PR（人工合入门）
- [ ] ROI 报告数据来自 S2-03 成本归一并可复现（含摊销公式）
- [ ] 手动 promote 通道：样本不足不阻塞但显著标注人工裁定；走 approval 留痕
- [ ] S1-02 NEEDS_REVIEW 7 条人工复核消费入口接通（复核后可进手动 promote）
- [ ] 既有 digestion/descriptors/approval/审计套件零回归；新增单测全绿、覆盖率 ≥80%
- [ ] 完整内化决策样例在验收报告可复现
