# START-S6-01 六面板扩展（分发壳 · 可直接复制）

> **本文件不是任务书。** 任务规格 → [`TASK-S6-01_六面板扩展.md`](TASK-S6-01_六面板扩展.md)（★ 执行会话必须完整阅读，尤其 **§零 数据源台账 + §0.2 移交项 U1–U10**）
> 批次与通用约定 → [`PARALLEL_并行铺开总表.md`](PARALLEL_并行铺开总表.md)
> 基线：`master` / `5984e03e`｜波次：**末波**（唯一任务）｜预估：8–12 人日
> ✅ **前置已全部就绪**：S2 / S3 / S4（01–04）/ S5（01–03）**全部结案**，全部面板数据源与后端约束均已交付。

---

## 一、开工前确认

1. `--base master`；worktree id 用 **`s601`**。
2. 本任务**无待裁定项**；它是整个 v7.2 重构计划的**最后一个任务**（S6 末波）。
3. 本任务**跨前端与后端两侧**：前端 `yunshu-ui`（React/TS）+ 后端只读/审批 API + Flask 侧审批区统一挂载。

---

## 二、启动提示词（整段复制给新会话）

```
【任务】TASK-S6-01 — 六面板扩展（消化流水线 / 能力地图 / 审批收件箱 / ROI / 自愈事故 / 记忆技能库 + 审计导出）
【任务书】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\TASK-S6-01_六面板扩展.md
          （★ 必须先完整阅读：§零 面板→数据源对照表 / §0.2 移交项 U1–U10 / §0.3 指标口径纪律 / §四 验收清单）
【批次总表】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\PARALLEL_并行铺开总表.md（worktree/门禁/硬约束/回报格式）
【所属计划】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\00_总览_审计结论与重构总计划.md
【上游设计文档】C:\Users\Administrator\Desktop\设计思路\CloudPivot_v7.2_final_合并归档(智谱审核版).md
                （相关章节：§7 六面板/七动作/UI 五坑/永不自动化五类；P7.2-24 组件名清单与可解释性；§5.7 机制6 UI 安全渲染；§13.3 联动）
【预估】8–12 人日
【状态】末波：S2–S5 全部结案，数据源齐备；无待裁定项

━━━ 一、工作区与隔离（强制）━━━
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s601 --base master
此后所有 git 操作在 .worktrees/s601/ 内；主工作区禁令与提交流程见批次总表 §三。
双远端推送：git push origin master && git push gitee master（保持同点）

━━━ 二、任务目标（摘要）━━━
在 yunshu-ui 工作台内扩展 v7.2 六面板（**不建独立 Web App**——UI 五坑红线④）：
1. 消化流水线（P0 展开）：泳道五列 轨迹采集 → 模式挖掘 → Skill 生成 → 验收 → 灰度
2. 能力地图（P1 折叠）：capability × provenance / risk / data_class / evolution.stage
3. 审批收件箱（P0）：支持**批量裁决**（同策略同风险一键批）；缺 undo_hint 不出现审批气泡
4. ROI / 成本（P1）：ROIChart（月省 / 投入 / 阈值）+ ACR/UTC 面板 + 审批衰减率
5. 自愈事故（P1，有事故自动展开）：IncidentCard + MTTR/MTTD + 备份健康卡片
6. 记忆 / 技能库（P2 折叠）
另：七动作 UI 接线（熔断/回滚/降级/摘除/审批/溯源 Diff/熔炉开关）、审计导出、可解释性 ReasonChain

━━━ 三、已就绪数据源（全部为已交付接口，勿自建聚合；详见任务书 §0.1）━━━
  消化：digestion/shadow.py::ShadowReport.to_dict()（p99_wall_* / overhead.shadow_overhead_ms / judge_kind）、
        digestion/internalize.py::InternalizeDecision.to_dict() / PromotePR.to_dict() / ManualReviewQueue.summary()、
        digestion/service.py::DigestionReport.as_dict()
  能力：descriptors/registry.py::list_with_trust()（provenance/risk/data_class/stage）
  审批：skills_mgmt/approval.py + PermissionDecision.to_dict()（matrix_hit/denied_by_matrix/requires_second_factor…）+
        object_type=stage.promote 真实链路
  成本：observability/utc.py::utc_daily/utc_window/utc_weekly/utc_snapshot/cost_daily_view/coefficient_table/
        shadow_overhead_audit/approval_decay_rate（**各带 calibration 块**）；COST_SOURCE_OF_TRUTH="events"
  自愈：self_healing/levels.py::IncidentCard.to_dict() + list_incidents() + healing.triggered（含 mttd_ms/mttr_ms）
  记忆：LayeredMemoryStore.recall() + taxonomy.recall_priority_key()
  其他：acr.py（acr_snapshot/daily/weekly）、model_degrade.py、escape.py、events.py、audit/chain.py::verify_chain()、
        §6.7 周报 8 项指标（S5-02）

━━━ 四、必须接收的移交遗留（U1–U10，详见任务书 §0.2）━━━
  U1 机制6 前端组件：TaintBadge / 审批区 Shadow DOM / 边界词确认 UI —— 按既有常量渲染，勿自定义：
     guardrails/boundary_words.py::boundary_state()（五类 / 60s 上限 / accepts_text_approval=False / single_action_bound=True）
     guardrails/safe_render.py::safe_render_state()（白名单 / CSP / 代理前缀 / TaintBadge 三个 class 名 / 审批区 z-index 固定值）
  U2 前端审批区现落在 Flask 侧（Shadow DOM 自治单元）→ 本任务统一挂载到工作台（保留 Shadow DOM）
  U3 越权告警聚合面板（actor_ip_hash + 窗口阈值口径已具备，面板消费未建）
  U4 熔断/回滚按钮 → release_bundle.rollback_bundle(bundle_hash, applier=...)，**L5 强制审批、组件子集一律拒绝**（不得旁路）
  U5 记忆→组装注入（策略 > 事实 > 偏好）按 taxonomy.recall_priority_key() 接线，或明确登记后续
  U6 状态灯 / 首屏性能**随本任务实测**（PERF_BUDGET_REBASED.md §五 有复测方案）
  U7 周期性评估调度点（evaluate_brakes()）接入或登记部署编排；allow_outbound() 有惰性兜底
  U8 策略/成本数字**必须标注口径**（决策本体 p99 0.047ms vs 全量埋点 3.76/7.97ms），不得混用
  U9 app_server.py::require_token 历史副本清理（或登记专项）
  U10 委派回收率接线（行契约 + 计算函数已交付）

━━━ 五、本任务特有硬约束（UI 五坑 + 口径纪律）━━━
1. **别把自动化改成人审**（面板只做呈现与触发，不新增人工步骤）
2. **别全量实时渲染**：虚拟滚动阈值 500 条；聚合 API <200ms、明细分页 <1s（实测）；P0 展开 / P1·P2 折叠
3. **别藏入口**：隐式入口（右键"沉淀为 Skill"等）必须留可点击记录
4. **别建独立 Web App**：一律在 yunshu-ui 工作台内扩展
5. **别让看板说谎**：每个数字可溯源到数据/审计；**样本 <20 只披露不考核；数据源缺位记 None，绝不以 0 冒充**；
   不可追溯的百分比禁止上屏（这是五坑⑤的红线，也是 S5-02 已确立的口径纪律）
6. **永不自动化五类**（发布/转账/删库/改权限/push --force）：UI 显式确认，绑定**单次 action + 60s 时效**，
   不接受任何文本形式的"已批准"（accepts_text_approval=False）
7. 写动作一律走既有审批（U4）；前端**不拥有额外权限**（S4-01 矩阵由后端单表校验）
8. 外来文本（MCP 返回/检索结果/子代理输出/文件内容）恒带 TaintBadge；系统信息只走结构化槽位

━━━ 六、验收与交付物 ━━━
交付：1) 后端只读/审批 API 组 + 前端类型化客户端；2) 六面板组件（StatusBadge/StagePipeline/CapabilityMap/
      ApprovalInbox/ROIChart/IncidentCard/ReasonChain）；3) 七动作 UI 接线 + 永不自动化五类确认 UI；
      4) 审计导出（含验签摘要）+ 安全渲染；5) U1–U10 逐条处置；6) TASK-S6-01_验收报告.md（含页面截图或 E2E 快照）；
      7) S6-01_交付结案报告_<日期>.md；8) 更新 00_总览 状态行（**本任务结案 = v7.2 重构计划全量收官**）
验收：逐条对照任务书 §四（含 U1–U10 与 §0.3 口径纪律）

━━━ 七、本地门禁 ━━━
  前端（yunshu-ui）：
    • npm run check（或 tsc -b --noEmit）+ eslint 零告警
    • vitest 全量（新增组件用例必加；既有用例零回归）
    • npm run build:flask 产物同步到 templates/yunshu.html（**否则后端页面看不到新面板**）
  后端：
    • 新增只读/审批 API 的单测（含权限：未授权访问被拒）
    • 邻接回归：pytest tests/unit/test_approval*.py tests/unit/test_audit*.py + 相关路由套件
    • kwarg 扫描两条：--path agent --min-risk HIGH；--path tests --min-risk HIGH
    • importlinter lint --config .importlinter；mypy 改动模块
  通用：真实提交场景验证 pre-commit；跑完门禁后 git status 检查产物漂移并还原

━━━ 八、上游已知坑 ━━━
1. **前端构建产物必须同步**：yunshu-ui 构建结果要同步到 Flask 模板，否则"面板已实现但页面看不到"
2. 面板数据若走 SSE/轮询，注意长轮询超时与空响应语义（既有 digest stream 有先例）
3. 审批区在 Flask 侧与 React 外壳并存 → 挂载时注意样式隔离（Shadow DOM 保留）与 z-index 固定值
4. 聚合 API 的性能要求（<200ms）在真实数据量下才暴露 → 用真实台账造数验证，勿只用空库
5. 前端用例易受时区/时间格式影响（既有跨午夜归档用例踩过）→ 用相对时间
6. 数字可追溯性检查：为每个指标在 UI 上保留"来源/公式/样本量/披露"入口（S5-02 已提供字段）

━━━ 九、回报格式 ━━━
见批次总表 §三"统一回报格式"；额外须附：六面板逐面板的数据源实证（非 mock）、U1–U10 处置结果、
以及"无不可追溯百分比"的自查结论
```

---

## 三、开工自查清单

- [ ] 已读任务书 §零（数据源台账）、§0.2（U1–U10）、§0.3（口径纪律）、§四（验收清单）
- [ ] `--base master`、id=`s601`；worktree 内工作
- [ ] 六面板数据全部来自真实接口（无 mock 数据）
- [ ] 虚拟滚动 500 / 聚合 <200ms / 明细分页 <1s 实测
- [ ] U1 常量复用（五类边界词 / 60s / z-index 未自定义）
- [ ] U4 熔断回滚走审批且 L5 强制（旁路不成立）
- [ ] 样本 <20 仅披露；缺数据记 None（不以 0 冒充）
- [ ] 永不自动化五类：单次 action + 60s 时效 + 不接受文本批准
- [ ] 前端构建产物已同步到 Flask 模板
- [ ] 前端 tsc/eslint/vitest + 后端 API 单测 + 邻接回归全绿
- [ ] 双远端同点推送
