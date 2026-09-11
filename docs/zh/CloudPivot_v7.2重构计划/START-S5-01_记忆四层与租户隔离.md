# START-S5-01 记忆四层与租户隔离（分发壳 · 可直接复制）

> **本文件不是任务书。** 任务规格 → [`TASK-S5-01_记忆四层与租户隔离.md`](TASK-S5-01_记忆四层与租户隔离.md)（★ 执行会话必须完整阅读）
> 批次与通用约定 → [`PARALLEL_并行铺开总表.md`](PARALLEL_并行铺开总表.md)
> 基线：`master` / `15eae00d`｜波次：**第一波**（无依赖摩擦）｜预估：4–6 人日

---

## 一、开工前确认

1. `--base master`；worktree id 用 **`s501`**。
2. 本任务**无待裁定项**；依赖 S2-01（统一 Trace 与 TraceContext）已结案。
3. 落点 `agent/memory/`、`agent/knowledge/` 与第一波其他任务**无文件重叠**，可放心并行。

---

## 二、启动提示词（整段复制给新会话）

```
【任务】TASK-S5-01 — 记忆四层 + 租户隔离 + 遗忘（对齐 v7.2 §4.3 / P7.2-08）
【任务书】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\TASK-S5-01_记忆四层与租户隔离.md（★ 必须先完整阅读）
【批次总表】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\PARALLEL_并行铺开总表.md
【所属计划】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\00_总览_审计结论与重构总计划.md
【上游设计文档】C:\Users\Administrator\Desktop\设计思路\CloudPivot_v7.2_final_合并归档(智谱审核版).md
                （相关章节：§4.3 记忆层四层+TTL+遗忘三触发 / §3.11 MemoryEntry / P7.2-08 租户映射 / §8 被遗忘权）
【预估】4–6 人日
【状态】依赖 S2-01（TraceContext 已交付 tenant/workspace/subject 字段）已结案；无待裁定项

━━━ 一、工作区与隔离（强制）━━━
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s501 --base master
此后所有 git 操作在 .worktrees/s501/ 内；主工作区禁令与提交流程见批次总表 §三。
双远端推送：git push origin master && git push gitee master

━━━ 二、任务目标（摘要）━━━
1. **记忆四层模型**：working（工作）/ fact（事实）/ preference（偏好）/ strategy（策略）+ TTL；
   条目模型对齐 §3.11（type / tenant_id / workspace_id / subject_id / scope / source_task_id /
   confidence / ttl_expires_at / forget_candidate）
2. **租户隔离（P7.2-08，铁律）**：workspace(repository) = 逻辑租户（tenant_id = workspace-hash）；
   subject_id = 登录用户；**事实/策略记忆按租户隔离**；**偏好跟随 subject 跨租户携带**；
   **个人偏好绝不污染企业策略记忆**（企业策略记忆 org 级只读下发）
3. **召回优先级**：project 事实 > global 偏好，同级新者胜（§4.3）
4. **遗忘三触发**：① 成功率 30 天 < 基线×0.7 ② 来源失效（descriptor deprecated/来源摘除）③ 删除权；
   快照留 30 天；被遗忘权：记忆物理删除、审计标识符匿名化（**删的是记忆不是证据**，S2-02 审计链保留）
5. **TTL**：到期自动降级为遗忘候选（工作记忆短 TTL、事实/策略长 TTL，可配）

━━━ 三、已就绪前置：勿重复实现 ━━━
  • **租户上下文（S2-01 已交付）**：agent/observability/trace_v2.py → TraceContext（tenant_id / workspace_id /
    subject_id）、derive_workspace_id()、MissingWorkspaceError（缺 workspace 的落账策略可参照其口径）
  • 记忆设施：agent/memory/（滚动摘要 / 黑匣子 / 压缩 / adapters）、agent/knowledge/（卡片 / 双链 / 检索）
  • 抽象层：agent/skills_mgmt/memory_abstractor.py
  • 质量数据源（供触发①）：agent/observability/trace_v2.py::UnifiedTraceStore（quality 统计）
  • 来源失效数据源（供触发②）：agent/descriptors/registry.py（stage / provenance / deprecated 状态）
  ★ 复用既有存储与检索，勿另建第二套记忆库

━━━ 四、本任务特有硬约束 ━━━
1. **写入 API 必须强制 tenancy**：缺 tenant/workspace 字段 → 拒绝或显式降级并标注（不得静默落全局）
2. **偏好跨越租户、事实/策略不跨越**——两条都要有单测断言（含"跨租户不可见"反例）
3. 企业策略记忆（若启用）为 org 级只读下发：个人写入必须被拒
4. 遗忘执行必须**先快照后删除**，并保证审计链不因删除而断裂（匿名化而非删除审计）
5. 不得改动既有记忆检索的公开行为（守不易）；新增分层以兼容方式叠加

━━━ 五、验收与交付物 ━━━
交付：1) 四层模型落地（§3.11 对齐）；2) 租户隔离矩阵与实现（含偏好随 subject 携带）；
      3) 遗忘三触发 + TTL + 快照 30 天 + 删除权匿名化；4) TASK-S5-01_验收报告.md（含隔离矩阵用例结果）；
      5) S5-01_交付结案报告_<日期>.md；6) 更新 00_总览 状态行
验收：逐条对照任务书 §四（四层类型 / 隔离矩阵 / 偏好携带 / 策略不可污染 / 三触发 / TTL / 删除权）

━━━ 六、本地门禁 ━━━
  • 相关套件：pytest tests/unit/test_memory*.py tests/unit/test_knowledge*.py tests/unit/test_memory_abstractor*.py
  • 邻接回归：pytest tests/unit/test_skills_mgmt.py（memory_abstractor 邻接）+ trace_v2 邻接
  • 全量抽查：pytest -m "not slow" -p no:randomly
  • kwarg 扫描两条：--path agent --min-risk HIGH；--path tests --min-risk HIGH
  • mypy 新增/改动模块 + 既有阻塞模块；importlinter lint --config .importlinter
  • 真实提交场景验证 pre-commit；跑完门禁后 git status 检查产物漂移并还原

━━━ 七、上游已知坑 ━━━
1. 记忆/知识类用例极易污染运行时数据目录（`data/` 下多项）→ 用例**必须显式传路径或加 autouse 会话级隔离**
2. 涉及加密/黑匣子的既有行为不要改（既有 AES-GCM 管道直接复用）
3. TTL/时间类断言避免依赖真实时钟边界（跨午夜/时区问题在既有轮次出现过）——用相对时间或注入时钟
4. 图/向量类检索的既有性能基线（RRF / 向量库 P99）不要退化，改动后跑邻接压测或至少跑相关单测

━━━ 八、回报格式 ━━━
见批次总表 §三"统一回报格式"；额外须附：租户隔离矩阵的**反例证据**（跨租户不可见 / 偏好可携带 / 策略不可污染）
```

---

## 三、开工自查清单

- [ ] 已读任务书全文
- [ ] `--base master`、id=`s501`；worktree 内工作
- [ ] 四层模型字段对齐 §3.11
- [ ] 隔离矩阵三条铁律均有正反用例（尤其"跨租户不可见"）
- [ ] 遗忘三触发各自有触发用例；快照先于删除
- [ ] 审计链在删除后仍可验签（匿名化而非删审计）
- [ ] 用例零运行时目录污染
- [ ] 本地门禁全绿；覆盖率 ≥80%
- [ ] 双远端同点推送
