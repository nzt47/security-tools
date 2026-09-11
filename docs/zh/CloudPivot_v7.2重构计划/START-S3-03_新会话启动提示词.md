# START-S3-03 新会话启动提示词（分发壳 · 可直接复制）

> **本文件不是任务书。** 职责分工如下，避免混淆：
> - **任务书（规格：做什么/怎么做/验收标准）** → [`TASK-S3-03_shadow与内化引擎.md`](TASK-S3-03_shadow与内化引擎.md)
> - **本文件（分发壳：怎么开工）** → 把 §二 的提示词整段复制给**新开的执行会话**；内含任务书路径、worktree 隔离命令、已就绪前置与移交遗留、本地门禁清单、上游已知坑、回报格式。
> 命名约定：`TASK-*.md` = 任务规格（一任务一份）；`START-*.md` = 该任务的分发启动包。
> 生成日期：2026-09-11｜生成时主工作区：分支 `master`、**无其他会话 worktree 占用**、工作树干净、与 `origin/master` 同步（开工前请以 `git log --oneline -1` 与 `git status` 复核当前 HEAD）。

---

## 一、分发前请确认三件事

1. **分支基准**：`new_session_worktree.py` 的 `--base` **默认是 `develop`**，本项目 v7.2 重构线**全部提交在 `master`**。**必须显式 `--base master`**，否则拿不到 `agent/digestion/` 全部前置代码。
2. **会话 id 格式**：`create` 几乎不校验，但 **`cleanup` 要求 id 形如 `s<数字>`**（如 `s303`）；用 `s303test` 这类字母结尾 id 会导致 cleanup 被拒、worktree 残留（已实测）。兜底：
   ```powershell
   git worktree remove .worktrees/<id> --force
   git branch -D <id>/main
   ```
3. **裁定项**：S3-03 **无待裁定项** —— S3-01/S3-02 均已结案，其全部前置与 6 项移交遗留（M1–M6）已在任务书 §零 交代清楚。可直接开工。

---

## 二、启动提示词（整段复制给新会话）

```
【任务】TASK-S3-03 — shadow 灰度 + 双跑 diff + 内化触发六条件引擎（含低流量手动 promote 通道）
【任务书】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\TASK-S3-03_shadow与内化引擎.md
          （★ 必须先完整阅读，特别是「§零 已交付资产复用清单」与「§0.2 必须接收的 S3-02 移交遗留 M1–M6」）
【所属计划】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\00_总览_审计结论与重构总计划.md
【上游设计文档】C:\Users\Administrator\Desktop\设计思路\CloudPivot_v7.2_final_合并归档(智谱审核版).md
                （相关章节：§4.5 Shadow 抽样/灰度 5%/转正/签名；§4.5.1 内化触发六条件；P7.2-01；T2 修正）
【预估工作量】6–10 人日
【状态】前置 100% 就绪（S3-01 验收 15/15、S3-02 验收 8/8，均已结案）——无待裁定项

━━━ 一、工作区与隔离（强制）━━━
仓库：C:\Users\Administrator\agent（当前分支 master；开工前用 git log --oneline -1 复核 HEAD）

若与其他会话并行，必须先建专属 worktree（项目强制规范，源自 2026-08-15 并行事故）：
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s303 --base master
    # ⚠ --base 默认 develop，本项目在 master，必须显式指定
    # ⚠ id 必须形如 s<数字>（s303 合法）
此后所有 git 操作都在 .worktrees/s303/ 内执行。

主工作区禁令（docs/zh/并行会话worktree隔离规范_20260815.md）：
  ✗ git checkout <branch>   ✗ git reset --hard   ✗ git add -A / git add .
  ✓ git add <具体文件>      ✓ 在 worktree 内提交

提交流程：worktree 内 add → commit → 回主工作区 git merge s303/main（或 cherry-pick）
          → 双远端推送（origin=github + gitee）保持同点 → cleanup s303（若报 id 非法则用 §一.2 兜底命令）
推送：git push origin master && git push gitee master
      （origin 若被 CI 自动提交（docs(architecture) 依赖图）顶住：先 git fetch 再 git pull --rebase origin master 后重推）

━━━ 二、任务目标（详见任务书；摘要）━━━
1. shadow 灰度运行：每日预算 = min(日均×15%, 50 次)，trace_id 哈希确定性抽样；shadow 只记录不接管真实执行；
   灰度 5%（开关默认关闭，开启需显式阈值）
2. 三层比对流水线（复用 S3-02 沙箱）：结构 schema 硬性 → 副作用集合硬性 → LLM-judge ≥0.85 软性 + 10% 人工抽检；
   任一层失败 → 样本记负例（供 R4"学错能力"劣化检测 → 降级/回退）
3. 内化触发六条件引擎（P7.2-01）：① digest_count ≥50 ② 月 samples ≥200 ③ ROI 为正
   （月省 > 自研一次性投入/12）④ 成功率 ≥ 上游×0.98 ⑤ p99 ≤ 上游 ⑥ privacy_gate=pass；
   **⑤⑥ 一票否决，①–④ 仅排序**；条件齐 → 自动创建 stage.promote PR（人工合入）
4. T2 修正——低流量手动 promote 通道：月 samples<200 的能力走"手动 promote + 人工评审"
   （复用 approval 留痕），避免消化空转
5. ROI 报告自动附于 PR（消费 S2-03 成本归一数据）

━━━ 三、已就绪前置：勿重复实现（S3-01/S3-02 交付）━━━
agent/digestion/ 现有模块（本次新增 shadow.py 与 internalize.py，放同一包内）：
  • cases.py：CaseSet / CaseStore / build_case_set() / EquivalenceCase / ProgramStep —— 判定集资产
  • sandbox.py：ReplaySandbox（双跑 + 三层比对 + 确定性自检 + record-and-replay 台账）、
                ConditionContext / evaluate_condition() / classify_condition() / is_destructive_condition()
  • gate.py：GateResult / GateConditionResult / branch_coverage() / baseline_from_traces() /
             baseline_from_ledger() / PassportStore / build_passport() —— 验收门与通行证
  • stage.py：stage 迁移门与执行（S3-02 已加 mirrored→shadow opt-in 通行证放行）
  • service.py：DigestionService.pipeline() 端到端门面
  • 其余：models / capability / cleaning / generalize / mining / generation（S3-01）
★ 灰度放行必须凭 PassportStore 通行证（勿自建开关绕过验收门）；双跑一律用 ReplaySandbox（勿自建第二套回放）

━━━ 四、必须接收的 S3-02 移交遗留（M1–M6，Owner 已登记，共 6 项）━━━
  M1 层③ judge 默认不是 LLM（确定性本地打分器，LLM-judge 经 judge= 注入，judge_kind 如实记录）
     → 本任务灰度期接入真实 LLM-judge（≥0.85）并标注 judge_kind
  M2 S3-02 的 p99 是"模型时钟量"（标称延迟累加）非墙钟
     → 六条件第⑤条必须用**真实墙钟 p99**，并在报告中标注 clock 口径
  M3 结构型分支（步骤出现/缺失、步数档位）登记为观察项，不阻断通行证；参数级分支必须覆盖
     → 沿用其分类规则，或先建"可回放的中止注入"机制后再纳入硬闸；不得静默忽略
  M4 用例↔候选适用性暂以 case.active + notes 表达，重生成后需重新施加
     → 引入**显式的 case↔candidate 适用性字段**
  M5 10% 人工抽检只产出清单，人工复核动作未执行（口径："未完成前不得视为已验收"）
     → 本任务落实人工复核动作并留痕（Owner 参与）
  M6 回放沙箱是进程内确定性执行模型，非容器隔离（§5.2"生成代码一律 Docker"属执行型产物要求）
     → 灰度若涉及真实执行，须明确隔离边界或显式声明沿用进程内模型及其风险

━━━ 五、硬约束（守不易 + 安全底线）━━━
1. 不改既有公开接口签名与行为；新增机制失败不得阻断主流程
2. **默认关闭**：灰度 5% 开关、重探调度（CP_DIGESTION_REPROBE_ENABLED）等一律默认关，显式阈值才开
3. **stage.promote "PR" 的边界**：产物必须是**本地可审阅形式**（分支/补丁 + PR 描述 + ROI 报告），
   **严禁自动 push 到远端、严禁自动合并**——人工合入是设计内的门
4. 门槛常量不得引入新的一套：沿用 S3-01 的 MIN_PATTERN_STEPS=2（成形）/ MIN_ASCENSION_STEPS=3（可升格）语义
5. 单测覆盖率 ≥80%；既有全量回归不得降级；可调参数走 .env（CP_* / DIGESTION_*），非法值回退默认
6. 术语纪律：v7.2「消化(digest)」= 内化流水线；云枢既有"评审"语义已在 S0-01 更名为 review/assess，勿混用
7. **口径纪律**：真实流量尚未达到"每能力 ≥20 条同类轨迹"，验收依赖 Seed Pack + 合成/回放数据（设计内的离线设施）——
   **不得声称"已实现真实能力内化"**；灰度真实收益须等流量上来后再评估

━━━ 六、验收与交付物 ━━━
交付：
  1) agent/digestion/shadow.py（ShadowRunner + 抽样 + 预算）
  2) 三层比对 CompareVerdict 流水线（含 10% 人工抽检）
  3) agent/digestion/internalize.py（六条件引擎 + stage.promote PR 产物 + ROI 报告）
  4) 低流量手动 promote 通道（走 approval 留痕）
  5) TASK-S3-03_验收报告.md（**含完整内化决策样例**：条件逐项打分 → ⑤⑥否决或全过 → PR 产物；附可复现命令）
  6) S3-03_交付结案报告_<YYYYMMDD>.md（结构参照 S3-02_交付结案报告_20260911.md：
     进度/成果清单/问题与解决方案/质量验证（本地门禁 + 远端 CI）/遗留（逐条带归属）/结案结论）
  7) 结案后更新 00_总览 S3-03 状态行 + 遗留消费安排
验收：逐条对照任务书 §四 评估清单（含 M1–M6 与通行证要求、未声称真实内化）全部满足

━━━ 七、本地门禁（推送前必跑，缺一不可）━━━
  • 相关套件：pytest tests/unit/test_digestion*.py（含新增 shadow/internalize 用例）
  • 邻接回归：pytest tests/unit/test_skills_mgmt.py tests/unit/test_approval*.py 等
  • 全量抽查：pytest -m "not slow" -p no:randomly（至少覆盖 digestion/skills/approval/orchestrator 邻接）
  • kwarg 扫描（两条都要跑）：
      python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH
      python scripts/scan_kwarg_conflicts.py --path tests  --min-risk HIGH
  • 类型：mypy 新增模块 + 既有阻塞模块（agent/env_config_manager.py / network_config.py）
  • 循环依赖：importlinter lint --config .importlinter（期望 kept / 0 broken）
  • 真实提交场景验证 pre-commit（勿无说明 --no-verify）
  • 跑完门禁后检查 git status：门禁脚本产物（如 docs/observability/boundary_coverage_report.json）可能漂移，需还原

━━━ 八、上游已知坑（S3-01/S3-02 实测，务必规避）━━━
1. **测试隔离**：涉及落盘存储（通行证/判定集/灰度台账）的用例必须**显式传存储路径**或加 autouse 会话级隔离 fixture，
   否则会写进运行时目录（S3-02 收尾轮次曾因此登记阻塞纪律项，已闭环，勿重犯）
2. **造数型用例**（批量 create/write 30+ 次）请主动加 @pytest.mark.timeout，避免 CI 高负载分片饿死误判
3. 本地 kwarg 扫描必须覆盖 agent/ **和 tests/**（S3-01 曾因只扫 agent/ 导致 HIGH 直到 CI 才暴露）
4. 阈值断言避免硬编码墙钟（CI 覆盖率插桩抖动），优先相对断言；**且性能类结论必须标明 clock 口径**（M2 同源）
5. 判定集重生成后需**重新施加** case↔candidate 适用性标注（M4 同源）；判定集无 TTL（S3-02 遗留 #6，属生产化）
6. 运行时台账（data/descriptors.json、data/digestion/* 等）已 gitignore，CI/换机不继承；如需重放用幂等脚本
   scripts/run_s3_01_ingest.py --execute

━━━ 九、完成后回报格式 ━━━
1) 交付物清单（文件路径 + 行数/用例数）
2) 任务书 §四 验收清单逐条核验（含 M1–M6 与通行证要求）
3) 质量证据：新增单测数/覆盖率、邻接回归结果、CI 终态（SHA + 全绿 job 数）
4) 遗留问题（逐条带归属与阻塞性判定）
5) 口径声明：明确"未声称真实能力内化（真实流量不足）"
6) 00_总览 状态行更新内容 + 双远端推送终态 SHA
```

---

## 三、新会话开工自查清单

- [ ] 已读任务书全文，特别是 §零 复用清单与 §0.2 的 M1–M6
- [ ] `--base master` 已显式指定；id 为 `s303` 形式
- [ ] worktree 内工作；主工作区无 checkout/reset/add -A
- [ ] 双跑复用 `ReplaySandbox`；灰度凭 `PassportStore` 通行证
- [ ] 六条件：⑤⑥ 一票否决逻辑正确；①–④ 仅排序
- [ ] PR 产物为本地可审阅形式，未自动 push / 未自动合并
- [ ] 灰度与调度开关默认关闭
- [ ] 涉及落盘的用例已做存储路径隔离
- [ ] kwarg 扫描双路径（agent + tests）已跑
- [ ] 验收报告含完整内化决策样例；未声称真实内化
- [ ] 双远端同点推送

---

## 四、任务在链条中的位置

```
S0 对齐 → S1 契约 → S2 数据基座（Trace/审计/事件）
                        ↓
              S3-01 消化流水线统一 ✅（agent/digestion/ 8 模块）
                        ↓
              S3-02 判定集 + 回放沙箱 + 验收门 ✅（cases/sandbox/gate + Seed Pack 14×43）
                        ↓
              S3-03 shadow 灰度 + 内化六条件引擎  ← 本次任务（S3 串行链最后一环）
                        ↓  完成后：v7.2 护城河闭环「轨迹 → 判定集 → 灰度 → 自动内化 PR」贯通
              S5-02 评测锚（L2 Core-50）→ S5-03 成本刹车 → S6-01 六面板
并行轨（可与本任务并行）：S4-01 审批矩阵 / S4-02 策略即代码 / S4-03 熔断回滚 / S4-04 subagent 真实现
```

> 现实提示：真实流量未达"每能力 ≥20 条同类轨迹"，本任务验收以 Seed Pack + 合成/回放数据为主，属设计内的离线设施；
> 真实内化收益需待流量积累后评估——**不要在验收结论中越过这一口径**。
