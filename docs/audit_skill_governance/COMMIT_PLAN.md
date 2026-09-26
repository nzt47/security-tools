# 提交方案（供 owner 审阅）—— 整批 40+ 张卡的未提交改动

> **状态**：**我没有提交任何东西**，分支仍在 `5c9ace10`。本文件只是**方案**。
> **前提**：已做的非破坏性快照见 `AUDIT_AND_PLAN.md` §11（git 对象 `f98db8b7…` + tar `C:\Users\Administrator\yunshu_snapshot_f98db8b7_20260926.tar`）。

---

## 0. 先说一个**不能掩盖的事实**

`git status` 共 **137 条**（79 改 / 56 未跟踪 / 2 已暂存）。**其中相当多的文件同时承载多张卡的改动**——
例如 `agent/skills_mgmt/registry.py` 同时含 **D2（启停入链）** 与 **G1-B（描述单源）**；
`agent/orchestrator/orchestrator.py` 同时含 **F3-1（易变尾簇）** 与其它卡；
`agent/skills_mgmt/service.py` 同时含 **D4 / F9 / G1-B / G1-C**。

⇒ **真正的「按卡拆分」需要 `git add -p` 级的逐 hunk 挑拣**（137 条 × 平均多 hunk）。
**本方案不假装能做到**：下面是**按文件归属的粗分组**，每个文件归到它的**主导卡**，
并在提交信息里**明确说明「部分文件含多卡 hunk」**。

---

## 1. 我推荐的两种做法（选一个）

### 方案 α（**推荐**）：**一个提交**，信息里指向审计报告

```
fix: 云枢技能治理与路由方案 V1.0 · 独立审计与重构实施（49 卡批次）

依据：docs/audit_skill_governance/AUDIT_AND_PLAN.md（含 §10 全量交付索引、§8 逐卡独立复核）
复核：44/49 卡经主审计独立复跑；3 卡被打回返工并修复；5 处红灯由主审计亲手修
闭环：E9 确认门三处绕过 / E14 工具检索向量腿恒降级 / 审计日根被测试写坏
约束：未提交期间全程保持未 commit；本提交为 owner 决定后的一次性落地
```

**为什么推荐**：这批改动**在语义上是一个整体**（审计 → 实施 → 独立复核 → 收口），
拆开反而**制造"某个中间提交是绿的"的错觉** —— 而实际中间态**从来没有被验证过**
（我是**先全部改完、再在终态上跑全量套件**的）。

### 方案 β：**6 个粗分组**（每个文件归主导卡）

| # | 提交信息 | 文件 |
|---|---|---|
| 1 | `fix(A): 服务存活与确认门除险` | `agent/server_port_guard.py`、`app_server.py`、`agent/tools/__init__.py`、`agent/tool_gate.py`、`agent/skills_mgmt/executor.py`、`start_yunshu.bat`、`scripts/watchdog_yunshu.py`、对应测试 |
| 2 | `fix(BCD): 口径/指标/审计/背压` | `agent/tools_prompt_guard.py`、`digital_life_persona.py`、`plugins/chat.py`、`agent/monitoring/prometheus.py`、`observability/tool_trace.py`、`orchestrator/routing_observability.py`、`agent/audit/*`、`agent/rate_limiter.py`、`agent/skills_mgmt/{file_store,service,enhancer,loader,searcher,store,vector_adapter,index_cache}.py`、`agent/model_router/adapters.py`、`scripts/audit_*.py`、`scripts/verify_*.py`、对应测试 |
| 3 | `feat(G1): 描述唯一源治理 + 5 条技能纳入` | `data/skills_repo/**`、`data/skills_descriptions_overlay.json`、`data/capability_manifest.json`、`agent/lines/callability.py`、`scripts/sync_capability_manifest.py`、`scripts/compare_skills_legacy_vs_repo.py`、`yunshu-ui/src/pages/hub/memory/skills.tsx`、`config.yaml`、`.github/workflows/*`、G1 测试 |
| 4 | `fix(E1/DET): 检索评测、向量腿、确定性` | `agent/tool_router.py`、`tool_router_hybrid.py`、`agent/workflow_learning/*`、`scripts/eval_route_conflict.py`、`scripts/probe_prefix_cache_interaction.py`、`agent/system_prompt_manager.py`、`agent/llm_monitor.py`、`memory/llm_service.py`、对应测试 |
| 5 | `chore(settings): 开关注册表收口` | `agent/settings/registry.py`、`tests/unit/test_settings_registry*.py` |
| 6 | `docs(audit): 审计与重构计划全套` + 数据卫生 | `docs/audit_skill_governance/`、`docs/rfc/云枢能力清单盘点表.md`、`.gitignore`、`data/learned_workflows.json`（移出跟踪） |

**β 的代价**：第 2 组会很大且跨域；且**任何一组单独 checkout 都不保证测试绿**（因为文件互相混卡）。

---

## 2. 两个**必须由你决定**的点

1. **`_tmp_rootcause_probe/evidence/probeC_*.json`（3 个文件）要不要进仓库？**
   它们是 Q4 探针的原始证据。**我倾向不进**（临时草稿目录），但若你要保留取证链，就并入第 6 组。
2. **`data/learned_workflows.json` 的 `D`（已暂存删除）要保留吗？**
   这是 F11 的处置：它是**运行期数据**，不该进仓库（磁盘文件仍在）。回滚 = `git reset -- data/learned_workflows.json`。

---

## 3. 我不建议做的事

- **不建议为了"好看"而拆成 49 个提交** —— 那需要逐 hunk 归属，**收益是叙事上的，代价是把没验证过的中间态说成"每步都绿"**。
- **不建议现在 `git checkout` 任何东西来做"干净对比"** —— 快照已经提供了等价能力（`git checkout f98db8b7 -- .`），而 checkout 工作区会**丢掉未快照之后的新改动**。
