# START-S3-02 新会话启动提示词（分发壳 · 可直接复制）

> **本文件不是任务书。** 职责分工如下，避免混淆：
> - **任务书（规格：做什么/怎么做/验收标准）** → [`TASK-S3-02_判定集与回放沙箱.md`](TASK-S3-02_判定集与回放沙箱.md)
> - **本文件（分发壳：怎么开工）** → 把 §二 的提示词整段复制给**新开的执行会话**；它内含任务书路径、worktree 隔离命令、本地门禁清单、S3-01 已知坑与回报格式。
> 命名约定：`TASK-*.md` = 任务规格（一个任务一份）；`START-*.md` = 该任务的分发启动包（可选，便于跨会话分发）。
> 生成日期：2026-09-11｜生成时主工作区状态：分支 `master`、工作树干净、与 `origin/master` 同步、**无其他会话 worktree 占用**（开工前请以 `git log --oneline -1` 与 `git status` 复核当前 HEAD）。
> 本模板可复用于后续任务（S3-03 / S4-* / S5-* / S6-01）：替换任务名与任务书路径即可。

---

## 一、分发前请确认三件事

1. **分支基准**：`new_session_worktree.py` 的 `--base` **默认是 `develop`**，而本项目 CloudPivot v7.2 重构线当前**全部提交在 `master`**（S0→S3-01 的提交链）。**必须显式 `--base master`**，否则新会话会在错误的基线上开发（拿不到 `agent/digestion/`、`agent/observability/trace_v2.py`、`agent/audit/chain.py` 等前置代码）。
2. **会话 id 格式**：`create` 几乎不校验 id，但 **`cleanup` 校验 id 必须是 `s<数字>` 形式**（如 `s302`、`s7`）；用 `s3test` 这类"字母结尾"的 id 会导致 **cleanup 拒绝执行、worktree 残留**（本次实测已复现）。⇒ 统一用 `--id s302`。若真被拒，兜底手动清理：
   ```powershell
   git worktree remove .worktrees/<id> --force
   git branch -D <id>/main
   ```
3. **是否真并行**：若新会话与本会话（或任何其它会话）同时存在，**必须走 worktree 隔离**（见提示词【工作区与隔离】段）；若确定串行，可直接在主工作区开工。
4. **裁定项是否已关闭**：S3-02 **无待裁定项**——其全部前置（S0 术语、S1 契约、S2 数据基座、S3-01 管道）均已结案。可直接开工。

> **本次已实测校验（2026-09-11）**：`create --id s302 --base master` 可正常创建，worktree 内 `agent/digestion/{service,models}.py`、`agent/observability/trace_v2.py`、`agent/audit/chain.py`、`agent/descriptors/registry.py` **全部可见**（基线正确）；脚本会自动把 `/.worktrees/` 追加进 `.gitignore`（本次已随文档一并提交，避免后续会话反复产生脏工作区）。

---

## 二、启动提示词（整段复制给新会话）

```
【任务】TASK-S3-02 — EquivalenceCase 判定集资产 + 回放沙箱 + 验收门量化
【任务书】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\TASK-S3-02_判定集与回放沙箱.md
          （★ 必须先完整阅读，特别是「§零 开工前置：S3-01 已交付的可直接复用资产」）
【所属计划】C:\Users\Administrator\agent\docs\zh\CloudPivot_v7.2重构计划\00_总览_审计结论与重构总计划.md
【上游设计文档】C:\Users\Administrator\Desktop\设计思路\CloudPivot_v7.2_final_合并归档(智谱审核版).md
                （相关章节：§3.1 EquivalenceCase 30-100 组 / §3.3 mirrored→shadow 唯一通行证 / §4.5 验收门硬闸 / P7.2-23 Seed Pack）
【预估工作量】5–8 人日
【状态】前置 100% 就绪（S3-01 已结案，验收 15/15）——无待裁定项，可直接开工

━━━ 一、工作区与隔离（强制）━━━
仓库：C:\Users\Administrator\agent（当前分支 master，HEAD c8e7407b，工作树干净）

若与其他会话并行，必须先建专属 worktree（项目强制规范，源自 2026-08-15 并行事故）：
    cd C:\Users\Administrator\agent
    python scripts/dev/new_session_worktree.py create --id s302 --base master
    # ⚠ 脚本 --base 默认是 develop，本项目重构线在 master，必须显式 --base master
    # ⚠ 会话 id 必须形如 s<数字>（s302 合法；s3test 会导致 cleanup 被拒，需手动兜底）
此后所有 git 操作都在 .worktrees/s302/ 内执行。

主工作区禁令（见 docs/zh/并行会话worktree隔离规范_20260815.md）：
  ✗ git checkout <branch>    ✗ git reset --hard / reset HEAD~N    ✗ git add -A / git add .
  ✓ git add <具体文件>       ✓ 在 worktree 内提交

提交流程：worktree 内 add → commit → 回主工作区 git merge s302/main（或 cherry-pick）
          → 双远端推送（origin=github + gitee）保持同点 → python scripts/dev/new_session_worktree.py cleanup s302
          （cleanup 若报 id 非法：git worktree remove .worktrees/s302 --force && git branch -D s302/main）
推送：git push origin master && git push gitee master
      （origin 若被 CI 自动提交（docs(architecture) 依赖图）顶住，先 git fetch 后 git pull --rebase origin master 再推）

━━━ 二、任务目标（详见任务书；此处为摘要）━━━
1. 判定集资产：EquivalenceCase / CaseSet / CaseStore —— 每个候选内化能力 30–100 组等价用例
   （输入 → 期望输出/副作用集合），带 origin_trace_id、独立于 Trace 存储（Trace 90 天过期不影响判定集）
2. Seed Pack 起步（P7.2-23）：≥12 技能 × ≥3 组预置用例，标记 provenance
3. 回放沙箱：确定性执行 + record-and-replay（副作用绝不双写真实环境）+ 双跑 diff 三层比对
   （结构 schema 硬性 → 副作用集合硬性 → LLM-judge ≥0.85 软性 + 10% 人工抽检）
4. 验收门硬闸（§4.5）：≥20 条回放全过 / 成功率 ≥ 基线×0.98 / p99 ≤ 上游 / 覆盖破坏性分支
   —— 四条件齐才发通行证（mirrored→shadow 与后续 shadow→internalized 的通行证）
5. 漂移重探：30 天或上游版本变化 → 简化探针 → schema 变化 → drifted → 判定集失效重生成
本任务不含 shadow 灰度运行（由 S3-03 承接）

━━━ 三、已就绪前置：勿重复实现（S3-01 交付 agent/digestion/）━━━
复用（清单见任务书 §零）：
  • TraceSet / Trajectory / TrajectoryStep（agent/digestion/models.py）—— 判定集用例来源数据
  • cleaning.py: same_task_key() / intent_key_for_trace() / clean_trajectory() / classify_outcome() / mark_negative()
  • CandidatePattern / PatternStep / BranchCondition / ParameterSlot —— 破坏性分支覆盖判定依据
  • SkillDraft / build_skill_draft() / compile_skill_body()（generation.py）—— 沙箱被评测候选（draft 态）
  • pattern_quality_gate() / converter_gate_constants() / solidify_min_rule_steps() —— 门槛口径
  • DigestionService.pipeline() / DigestionReport（service.py）—— 串接入口
  • stage_migrate() / evaluate_migration()（stage.py）—— 验收门通过后的 stage 推进入口
本任务新增文件放在同一包内：agent/digestion/cases.py、sandbox.py（勿另起新包）

S3-01 移交给本任务的扩展点（其结案报告 §5 遗留 #4）：
  决策树特征当前仅含「可选/骨架步骤有无 + 步数档位」，参数级条件（如"路径含 test 时才需 review"）未纳入；
  若判定集需要参数级分支覆盖，在本任务内扩展。

━━━ 四、硬约束（守不易）━━━
1. 不改既有公开接口签名与行为；新增机制失败不得阻断主流程（advisory 语义）
2. 产物一律 draft 态，不自动发布、不越过审批门
3. 门槛常量不得引入第三套：沿用 S3-01 已统一的 MIN_PATTERN_STEPS=2（成形）与 MIN_ASCENSION_STEPS=3（可升格）语义
4. 单测覆盖率 ≥80%；既有全量回归不得降级（新增用例不得引入顺序污染）
5. 可调参数走 .env / config（CP_* / DIGESTION_* 等），非法值回退默认
6. 术语纪律：v7.2「消化(digest)」= 内化流水线；云枢既有"评审"语义已在 S0-01 更名为 review/assess——勿混用

━━━ 五、验收与交付物 ━━━
交付：
  1) agent/digestion/cases.py + sandbox.py（+ 必要的 models 扩展）
  2) Seed Pack（≥12 技能 × ≥3 组）与判定集存储
  3) TASK-S3-02_验收报告.md（**含一个真实能力的端到端样例**：判定集 → 回放 → 验收门结果，
     附可复现命令与输出）
  4) S3-02_交付结案报告_<YYYYMMDD>.md（结构参照 S3-01_交付结案报告_20260911.md：
     进度/成果清单/遇到问题与解决方案/质量验证（本地门禁 + 远端 CI）/遗留问题（逐条带归属）/结案结论）
  5) 结案后更新 00_总览 中 S3-02 状态行（并登记遗留消费安排）
验收：逐条对照任务书 §四 评估标准（8 条）全部满足

━━━ 六、本地门禁（推送前必跑，缺一不可）━━━
  • 相关套件：pytest tests/unit/test_digestion*.py（按实际新增文件调整）
  • 邻接回归：pytest tests/unit/test_skills_mgmt.py tests/unit/test_descriptors*.py 等
  • 全量抽查：pytest -m "not slow" -p no:randomly（至少覆盖 digestion/skills/descriptors/orchestrator 邻接）
  • kwarg 扫描：python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH
                 python scripts/scan_kwarg_conflicts.py --path tests  --min-risk HIGH   ← 两个都要跑！
  • 类型：mypy 新增模块 + 既有阻塞模块（agent/env_config_manager.py / network_config.py）
  • 循环依赖：importlinter lint --config .importlinter（期望 2 kept / 0 broken）
  • 真实提交场景验证 pre-commit（check-index-isolation 等 9 个 hook）
  • 严禁无说明的 --no-verify

━━━ 七、S3-01 已知坑（务必规避）━━━
1. 造数型用例（批量 create/write 30+ 次）请**主动加 @pytest.mark.timeout**，否则 CI 高负载分片下线程饿死会误判为产品缺陷
2. 本地 kwarg 扫描必须覆盖 agent/ **和 tests/**（S3-01 曾因只扫 agent/ 导致 HIGH 直到 CI 才暴露）
3. 性能/阈值断言避免硬编码墙钟（CI 覆盖率插桩会抖动），优先相对断言（如 a < b*2）
4. 门控口径已统一（MIN_PATTERN_STEPS=2 / MIN_ASCENSION_STEPS=3 已与 solidify 对账），勿再分叉
5. 运行时台账（data/descriptors.json 等）已 gitignore、CI/换机不继承；如需重放，用幂等脚本
   scripts/run_s3_01_ingest.py --execute（S3-01 遗留 #10）

━━━ 八、完成后回给 Owner 的回报格式 ━━━
1) 交付物清单（文件路径 + 行数/用例数）
2) 任务书 §四 验收清单逐条核验结果（含证据命令）
3) 质量证据：单测数/覆盖率、邻接回归结果、CI 终态（哪个 SHA、哪些 job 全绿）
4) 遗留问题（逐条带归属任务与阻塞性判定）
5) 00_总览 状态行更新内容
6) 双远端推送终态 SHA
```

---

## 三、新会话开工自查清单（可放在会话开头自检）

- [ ] 已读任务书全文，特别是 §零 复用清单
- [ ] `--base master`（不是 develop）已显式指定
- [ ] worktree 内工作（若并行）；主工作区无 checkout/reset/add -A
- [ ] 新增文件落在 `agent/digestion/` 包内，未另起新包
- [ ] 门槛常量未引入第三套
- [ ] 产物 draft 态，无自动发布路径
- [ ] 本地门禁双路径 kwarg 扫描已跑（agent + tests）
- [ ] 验收报告含端到端可复现样例
- [ ] 结案报告含遗留逐条归属
- [ ] 双远端同点推送

---

## 四、与上游计划的接口（供新会话理解自己在哪一环）

```
S0 对齐 → S1 契约 → S2 数据基座（Trace/审计/事件）
                          ↓
                   S3-01 消化流水线统一 ✅ 已结案（agent/digestion/ 已交付）
                          ↓
                   S3-02 判定集与回放沙箱  ← 本次任务（串行链中段，关键路径）
                          ↓
                   S3-03 shadow 灰度 + 内化六条件引擎（本任务交付的判定集/验收门是其前置）
                          ↓
                   S5-02 评测锚（L2 Core-50）→ S5-03 成本刹车 → S6-01 六面板
并行轨：S4 治理安全（S4-01 审批矩阵 / S4-02 策略即代码 / S4-03 熔断回滚 / S4-04 subagent 真实现）
```

> 现实提示（S3-01 遗留 #1）：`borrowed→mirrored` 需每能力累积 ≥20 条同类轨迹，**真实流量目前不足**，
> 故本任务验收主要依赖 Seed Pack + 回放/合成数据——这是设计内的（判定集与沙箱本就是离线设施），
> 不影响开工；但**不要**在验收中声称"已实现真实能力内化"。
