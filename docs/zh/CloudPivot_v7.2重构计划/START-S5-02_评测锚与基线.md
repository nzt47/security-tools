# START-S5-02 评测锚与基线（分发壳 · 可直接复制）

> **本文件不是任务书。** 任务规格 → [`TASK-S5-02_评测锚与基线.md`](TASK-S5-02_评测锚与基线.md)（★ 执行会话必须完整阅读）
> 批次与通用约定 → [`PARALLEL_并行铺开总表.md`](PARALLEL_并行铺开总表.md)（worktree/门禁/硬约束/回报格式全文）
> 基线：`master` / `15eae00d`｜波次：**第一波**（无依赖摩擦）｜预估：4–6 人日

---

## 一、开工前确认

1. `--base master`（默认 develop 会拿不到 S2/S3 前置代码）；worktree id 用 **`s502`**（须 `s<数字>` 形式）。
2. 本任务**无待裁定项**；依赖 S2-03 与 S3 已全部结案。
3. **它是当前关键路径的枢纽**：L2 Core-50 建立后，S5-03 的成本系数校准（Owner 裁定 C）才会被解锁——被依赖方，优先度高。

---

## 二、启动提示词（整段复制给新会话）

```
【任务】TASK-S5-02 — L0-L3 评测锚与基线（打破自验收循环）
【任务书】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\TASK-S5-02_评测锚与基线.md（★ 必须先完整阅读）
【批次总表】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\PARALLEL_并行铺开总表.md（worktree 命令/门禁/硬约束/回报格式）
【所属计划】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\00_总览_审计结论与重构总计划.md
【上游设计文档】C:\Users\Administrator\Desktop\设计思路\CloudPivot_v7.2_final_合并归档(智谱审核版).md
                （相关章节：§6.5 评测分层 L0-L3 / §6.7 指标字典与 SLO / §6.6 埋点清单）
【预估】4–6 人日
【状态】依赖 S2-03 与 S3（S3-01/02/03）均已结案；无待裁定项

━━━ 一、工作区与隔离（强制）━━━
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s502 --base master
此后所有 git 操作在 .worktrees/s502/ 内。主工作区禁令与提交流程见批次总表 §三。
双远端推送：git push origin master && git push gitee master（保持同点）

━━━ 二、任务目标（摘要）━━━
1. **L0 锚**：20 条**人工冻结**用例（哈希锚定、**系统不可写**——独立于系统数据目录、只读位置/签名校验），
   作为打破"自验收循环"的客观标尺；产出 run_l0 执行器
2. **L1 最小集 10 条**（快路径回归用）
3. **L2 Core-50**：三类种子场景（S1 修 bug / S2 懂代码库 / S3 提交）扩展至 50 条，**作为 UTC 基线唯一依据**
4. **L3 Golden-80**：本任务只需定义框架（目录/运行器/周期），实际扩充入 M7+
5. **§6.7 指标字典**：消化吞吐 / 内化转化率 / 委派回收率 / 技能成功率 / 路由准确率（≥5 项可计算），
   产出周报脚本 scripts/report_slo_weekly.py

━━━ 三、已就绪前置：勿重复实现 ━━━
  • 指标埋点：agent/observability/acr.py（record_task_closed / record_intervention / acr_daily / acr_weekly / acr_snapshot）
               agent/observability/utc.py（utc_daily / utc_weekly / utc_window / coefficient_table）
  • 轨迹台账：agent/observability/trace_v2.py（UnifiedTraceStore: query / list_by_capability / task_summary）
  • 事件流：agent/observability/events.py（EventEnvelope / SCHEMA_NAME=events.v1）
  • 消化与灰度（S3 交付）：
      agent/digestion/service.py（DigestionService.pipeline / DigestionReport）
      agent/digestion/gate.py（GateResult / PassportStore / baseline_from_traces）
      agent/digestion/internalize.py（InternalizeDecision / PromotePR）
      agent/digestion/shadow.py → **ShadowReport.p99_wall_***（真实墙钟 p99）+ **ShadowLedger.daily_average()**（能力级样本量趋势）
  ★ 指标计算一律复用上述接口，**勿另建第二套计数/聚合逻辑**

━━━ 四、必须接收的移交遗留（已写入任务书 §步骤 3）━━━
  • S2-03 遗留 #3：ACR 意图/难度为启发式（词典 + 长度阈值）→ **L2 基线建立后**以基线数据拟合真实难度权重，
    切换前保持"只披露不考核"（对齐审计 T5 结论）
  • S2-03 遗留 #4：探索满意度为代理指标（关闭成功率）→ 本任务定义**正式口径**（建议 👍 率 + 任务闭环率双列），
    并在报告显式披露替代关系
  • S3-03 已交付：`ShadowReport.p99_wall_*` 与 `ShadowLedger.daily_average()` 直接作为 L2 性能/样本量基线输入；
    由此**解锁 S5-03 的成本系数校准触发条件**

━━━ 五、本任务特有硬约束 ━━━
1. **L0 必须系统不可写**：用例存储独立于系统数据目录，自动化流程无权修改；哈希锚定入 release manifest
2. 用例判定标准**机械可验优先**（避免"靠感觉通过"）；每类种子场景 ≥2 条
3. L1/L2 新增用例避免与既有慢用例混入同一 CI 分片（注意造数型用例加 timeout 标记）
4. 指标口径须写明数据源与计算公式，**不可追溯的数字一律不出现在报告**

━━━ 六、验收与交付物 ━━━
交付：1) L0 锚 20 条（独立只读存储 + 哈希锚定 + run_l0）；2) L1 10 条 + L2 Core-50 + L3 框架；
      3) §6.7 指标计算脚本（周报）；4) TASK-S5-02_验收报告.md（含 L0 首次运行结果与逐条清单）；
      5) S5-02_交付结案报告_<日期>.md（结构参照 S3-03_交付结案报告）；6) 更新 00_总览 状态行
验收：逐条对照任务书 §四（含 S2-03 #3/#4 与"未另建重复计数逻辑"）

━━━ 七、本地门禁 ━━━
  • 本任务相关套件：pytest tests/unit/test_eval*.py tests/unit/test_metrics*.py（按实际新增调整）
  • 邻接回归：pytest tests/unit/test_acr*.py tests/unit/test_utc*.py（如存在）+ skills/digestion 邻接
  • 全量抽查：pytest -m "not slow" -p no:randomly
  • kwarg 扫描两条：--path agent --min-risk HIGH；--path tests --min-risk HIGH
  • mypy 新增模块 + 既有阻塞模块；importlinter lint --config .importlinter
  • 真实提交场景验证 pre-commit；跑完门禁后 git status 检查产物漂移并还原

━━━ 八、上游已知坑（S2/S3 实测）━━━
1. 涉及落盘的用例必须**显式传存储路径或加 autouse 会话级隔离**（S3-02/S3-03 两次因此产生运行时区污染）
2. 阈值/性能断言避免硬编码墙钟（CI 覆盖率插桩抖动），优先相对断言，且**必须标明 clock 口径**
3. 造数型用例加 @pytest.mark.timeout，避免 CI 高负载分片饿死误判
4. 判定/评测类用例不要依赖真实 LLM 凭证（本环境可能无凭证）——需要时如实标注回落口径

━━━ 九、回报格式 ━━━
见批次总表 §三"统一回报格式"（交付物 / 验收逐条 / 质量证据 / 遗留 / 总览更新 / 双远端 SHA）
```

---

## 三、开工自查清单

- [ ] 已读任务书全文（含 §零 与 §步骤 3 的移交项）
- [ ] `--base master`、id=`s502`；worktree 内工作
- [ ] L0 用例独立存储且**系统不可写**（扰动尝试被拒的用例）
- [ ] L2 Core-50 覆盖三类种子场景，明确清单与 UTC 基线口径
- [ ] 指标 ≥5 项可计算且数据源可追溯（复用 `acr.py`/`utc.py`，未另建聚合）
- [ ] S2-03 #3/#4 处置路径已写清（拟合时机 / 正式口径 + 披露）
- [ ] 本地门禁双路径 kwarg 扫描已跑；覆盖率 ≥80%
- [ ] 双远端同点推送
