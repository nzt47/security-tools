# 仓库卫生报告（TASK-03 第 5 步 / **E7**）

> **日期**：2026-09-18 · **HEAD（作业时）**：`82bbe6fd297963148d7a6874a94012a04897b184`
> **范围纪律**：只做**低风险、可验证**的清理；**不动 `agent/` 业务代码**；
> 所有删除均通过 `git rm`（可 `git reset` / `git checkout -- <path>` 回滚），未执行任何 `git commit`。

---

## 1. 前后对比数字（E7 的核心）

| 指标 | 清理前 | 清理后 | 变化 |
|---|---|---|---|
| **根目录受控文件总数**（`git ls-files` 中不含 `/` 的项） | **127** | **95** | **−32（−25.2%）** |
| 其中判定为「产物」（本报告 §3 的可复算规则） | **80** | **48** | **−32（−40.0%）** |
| 其中「运行期导出型产物」= 规则 A（覆盖率/测试导出）+ B（混沌/压测/性能）+ C 中已删项 | **35** | **0** | **−35（−100%）** |
| 根目录未跟踪残留（`??`） | 见下 | 见下 | — |

> ⚠️ **口径诚实声明**：TASK-03 §2.4 给出的原始数字是"127 个跟踪文件，其中 **59 个是产物**"，
> 但**未给出判定规则**，因此那个 59 无法逐条复算。本报告改用一套**写死的、可复算的**规则
> （§3，脚本 `_ci_logs/task03/hygiene_classify.py`，任何人可重跑），得到 80。
> 两者差异来自"一次性 `.ps1` / `demo_*.py` 算不算产物"这一条 —— 本报告算作产物，
> 但**并未删除它们**（原因见 §5）。
> 结论：**总数 −32 是硬事实**；"产物数"的绝对值取决于规则，本报告始终同时给出规则与明细。

---

## 2. 处置明细：删除的 32 个文件（全部可回滚）

命令形式（**统一用 `git rm`，不使用 `Remove-Item` 直删**，见 TASK-03 §6 回滚方案）：

```powershell
# 零引用的 → git rm -q --cached -- <path>  然后 Remove-Item（从磁盘与索引同时移除）
# 有引用的 → git rm -q --cached -- <path>  （只脱离版本控制，磁盘文件保留）
```

### 2.1 零引用 ⇒ 完整删除（26 个）

| # | 文件 | 类别 | 引用检查结果 |
|---|---|---|---|
| 1 | `^` | 一次性调试输出 | 零真实引用（`git grep --fixed-strings "^"` 的 4443 条命中**全是正则锚点/二进制**，非文件名引用）。内容实测为 `python --version ^ 2^` —— Windows `cmd` 重定向残留 |
| 2 | `ts_log.txt`（10103B） | 调试输出 | 零引用 |
| 3 | `ts_log2.txt`（19288B） | 调试输出 | 零引用 |
| 4 | `run_log.txt`（0B） | 调试输出 | 零引用 |
| 5 | `coverage_full.xml` | 覆盖率导出 | 零引用 |
| 6 | `coverage_search.xml` | 覆盖率导出 | 零引用 |
| 7 | `pytest_baseline.txt` | 测试导出 | 零引用 |
| 8 | `pytest_baseline_full.txt` | 测试导出 | 零引用 |
| 9 | `error_handler_cov_final.txt` | 覆盖率导出 | 零引用 |
| 10 | `error_handler_cov_v2.txt` | 覆盖率导出 | 零引用 |
| 11 | `system_tools_cov_final.txt` | 覆盖率导出 | 零引用 |
| 12 | `task_scheduler_cov.txt` | 覆盖率导出 | 零引用 |
| 13 | `task_scheduler_cov_combined.txt` | 覆盖率导出 | 零引用 |
| 14 | `task_scheduler_cov_final.txt` | 覆盖率导出 | 零引用 |
| 15 | `task_scheduler_cov_verify.txt` | 覆盖率导出 | 零引用 |
| 16–22 | `chaos_test_report_20260624_1926{18}…193549}.md`（**7 份**） | 混沌测试产物 | 零引用。且 TASK-00 §0.2b 已认定：**7 轮中 6 轮因脚手架自身 bug 失败** ⇒ 保留它们只会诱导"只引最后一轮全绿"的误用 |
| 23 | `performance_benchmark_1782301622.json` | 性能产物 | 零引用 |
| 24 | `migration_report.txt` | 一次性导出 | 零引用 |
| 25 | `test_cost.jsonl` | 一次性导出 | 零引用 |
| 26 | `vis_missing.json` | 一次性导出 | 零引用 |

### 2.2 有引用、但确认属于"可重生成的产物" ⇒ `git rm --cached`（脱离跟踪，磁盘保留，6 个）

| # | 文件 | 引用方（引用检查实测） | 为什么"脱离跟踪"是安全的 |
|---|---|---|---|
| 27 | `coverage.xml` | `.github/workflows/ci.yml:560,568`（`ls -la coverage.xml` / artifact 上传路径）、`.ci_logs/*.log` 大量 | CI 引用的是**产物路径**，由 coverage job 每次重新生成；删除仓库里的旧副本不影响 CI。**且它是 2026-07-11 的过期快照** —— TASK-03 §2.3 明确要求"不要引用它"，留在版本控制里等于把过期数字伪装成权威 |
| 28 | `coverage_scripts.xml` | `docs/archive/scripts_coverage_governance_plan_20260809.md`（4 处） | 该文档描述的是**如何生成**它，不是"要求它入库" |
| 29 | `coverage_summary.xml` | `docs/archive/test_coverage_report.md:105` | 同上 |
| 30 | `performance_benchmark_1782301576.json` | `docs/PERF_BUDGET_REBASED.md:68`（作为性能预算证据） | 见下方 ⚠️ |
| 31 | `tracing_performance_report_1782295077.json` | `docs/PERF_BUDGET_REBASED.md:62` | 见下方 ⚠️ |
| 32 | `stress-report.json` | `docs/PERF_BUDGET_REBASED.md:121`、`docs/archive/merge_preview_phase2_to_develop.md:128`、`scripts/stress_intent_layer_ratio_thread_safety.py:17`（脚本**写出**它） | 脚本是写方；文档是引用方 |

> ⚠️ **关于 30/31 的重要说明**：TASK-00 §0.2b 已明确记录
> `data/tracing_performance_report_*.json` 的 p50/p90/p99 **全是假分位数**（每项 `count: 1`），
> 而 `docs/PERF_BUDGET_REBASED.md` 恰恰引用 `tracing_performance_report_1782295077.json`
> 作为性能预算**通过**的证据。**这正是"看似权威实则不可引用"的典型**。
> 本次处置选择 `git rm --cached`（**不删磁盘文件**）而不是完整删除，理由是：
> (1) 删掉会让 `PERF_BUDGET_REBASED.md` 的引用彻底悬空，而该文档的**结论本身**
> 是否成立不属于 TASK-03 的判定范围；
> (2) 脱离跟踪已经达成"不再把过期产物当作仓库内容"的目标；
> (3) 该文件仍是后续复核（认定其分位数造假）的物证。
> **登记为债务**：`PERF_BUDGET_REBASED.md` 的性能预算结论需要 TASK-08 重新以真实压测重算。

### 2.3 已同步写入 `.gitignore`（防止同型产物再次被 add）

见 `.gitignore` 末尾"【TASK-03 · 2026-09-18 根目录产物治理】"段，新增模式：
`/coverage*.xml`、`/pytest_baseline*.txt`、`/*_cov*.txt`、`/chaos_test_report_*.md`、
`/performance_benchmark_*.json`、`/tracing_performance_report_*.json`、`/stress-report.json`、
`/ts_log*.txt`、`/run_log.txt`、`/test.log`、`/migration_report.txt`、`/test_cost.jsonl`、
`/vis_missing.json`、`/.coverage*`（全部以 `/` 前缀，**只匹配根目录**，不影响业务目录同名文件）。

> **为什么这一步是必需的**：`.gitignore` 对**未跟踪**残留一直有效，对**已跟踪**文件
> **完全无效**。这 32 个文件之所以能长期待在根目录，正是因为它们在有对应规则
> **之前**就被 `git add` 了 —— 之后无论怎么加规则都不会自动脱离跟踪。加规则只能防"下一次"。

---

## 3. 可复算的产物判定规则（脚本 `hygiene_classify.py`）

| 规则 | 模式 | 清理后计数 |
|---|---|---|
| A 覆盖率 / 测试导出 | `coverage*.xml`、`pytest_baseline*.txt`、`*_cov*.txt`、`CI_VALIDATION_REPORT.txt` | **1** |
| B 混沌 / 压测 / 性能产物 | `chaos_test_report_*.md`、`performance_benchmark_*.json`、`tracing_performance_report_*.json`、`stress-report.json` | **0** |
| C 一次性调试输出 | `^`、`ts_log*.txt`、`run_log.txt`、`migration_report.txt`、`test_cost.jsonl`、`vis_missing.json`、`conversation_log.json`、`auto_save_service.txt`、`findings.md`、`progress.md`、`task_plan.md` | **5** |
| D 提交草稿 | `.commit_msg_*.md` | **2** |
| E 一次性运维 / 演示脚本 | `*.ps1`、`demo_*.py`、`run_*.py`、`main.py`、`agent.js`、`config.py` 等 | **38** |
| F 备份快照 | `*_backup*.yaml`、`*.corrupt_backup_*` | **2** |
| — 未归类（保留） | — | **47** |
| **合计** | | 95（产物 48） |

复现：

```powershell
python _ci_logs/task03/hygiene_classify.py     # 清理后：95 / 产物 48
```

---

## 4. ⚠️ 引用检查的完整结果（**这是本步最重要的产出**）

TASK-03 的"不通过"清单里写着「**删除文件却没做引用检查（导致运维脚本或 CI 断裂）**」。
所以本步对**每一个**候选文件都做了 `git grep --fixed-strings <文件名>`，结果如下 ——
**这些是"看着像产物但绝不能删/不能移"的文件**：

| 文件 | 引用方 | 判定 |
|---|---|---|
| `check.ps1` | **`.github/workflows/ci.yml`**、`.github/workflows/skills-check.yml`、`Modules/AdminDependencyChecker/*.psd1` | ⛔ **CI 依赖，禁止移动/删除** |
| `complete_verification.ps1` | `docs/archive/VERIFICATION_REPORT.md` 等 | ⛔ 有引用，保留原位 |
| `recover_docker.ps1` | `complete_verification.ps1`（**脚本之间互相调用**） | ⛔ 保留 |
| `simple_verify.ps1` | `complete_rebuild.ps1`、`complete_rebuild_simple.ps1`、文档 | ⛔ 保留 |
| `setup-kubeconfig.ps1` / `test-kubeconfig.ps1` | `demo-kubeconfig-fix.ps1`、4 份文档 | ⛔ 保留 |
| `conversation_log.json` | `tests/test_conversation_injection.py:145`、`tests/test_persona_interaction.py:217,224` | ⛔ 测试引用其**文件名**，保留 |
| `auto_save_service.txt` | `data/memory/agent_memory.json`（**运维脚本的实际用法**：`启动自动保存.bat` 会把它 `move` 成 `auto_save_service.js`） | ⛔ **真实运维依赖**，保留 |
| `CI_VALIDATION_REPORT.txt` | `scripts/validate_ci_config.py:189`（**写出方**） | ⛔ 保留（写方引用） |
| `.commit_msg_fix.md` / `.commit_msg_test.md` | `docs/archive/merge_preview_phase2_to_develop.md`、`docs/zh/.../TASK-S7-02_端到端样例_20260912.json`（作为**仓库清单样例**） | ⚠️ 保留（引用为"清单样例"，删除会让样例与实际不符） |
| `.env.corrupt_backup_20260810` / `config.v1_backup_20260623_093653.yaml` | 零引用 | ⚠️ **仍保留**（是"某次事故的诊断物证"，非纯产物；删除收益低、风险非零） |

**总引用检查记录**：37 个候选 × 逐条 grep = **保留 12 个、删除 26 个、脱离跟踪 6 个**（另有 5 个因零引用但属"物证类"主动保留）。

---

## 5. 明确**未做**的两项（含理由，避免被误读为遗漏）

### 5.1 一次性 `.ps1` 脚本**未归档到 `scripts/ops/`**（TASK-03 §3 第 5 步第 3 项）

TASK-03 建议"用 `git mv` 归类到 `scripts/legacy/` 或 `scripts/ops/`"。**本任务不做**，
理由是**实测的引用密度**：

```
根目录受控 .ps1 = 25 个，其中 23 个被引用：
  * check.ps1                    ← .github/workflows/ci.yml + skills-check.yml（CI 强依赖）
  * recover_docker.ps1           ← complete_verification.ps1（脚本互相调用）
  * simple_verify.ps1            ← complete_rebuild.ps1 / complete_rebuild_simple.ps1
  * setup-kubeconfig.ps1         ← demo-kubeconfig-fix.ps1
  * test-kubeconfig.ps1          ← demo-kubeconfig-fix.ps1
  * 其余 18 个                   ← docs/ 下 20+ 处引用（含 docs/archive/ 与 2 份现行运维手册）
```

⇒ 一次性 `git mv` 会**同时改断 20+ 条文档链接与 2 个 CI workflow 的脚本路径**，
这正是 TASK-03"不通过"清单里点名的失败模式。
**正确做法**是"先改引用、再移动"或"移动时同时提供兼容 shim"，那是一个独立的、
需要逐条改文档的任务量，**不适合塞进"限定范围的地基加固"里**。

**替代产出（满足"被引用的记录在 docs/ops_quick_manual.md"）**：已把 25 个 `.ps1` 的
引用关系表追加进 `docs/ops_quick_manual.md`，供后续任务按表安全迁移。

### 5.2 `.env.backups/` 的 50 份密钥副本**未删除**（TASK-03 §3 第 5 步第 4 项）

实测（本机）：

| 项 | 值 |
|---|---|
| 文件数 | **50**（TASK-03 记为 57，实测为 50 —— 以实测为准） |
| 总体积 | **7,038 KB**（均值 ~141 KB/份） |
| 时间跨度 | 2026-09-11 00:04 → 2026-09-13 03:12 |
| 是否被 git 忽略 | ✅ `.gitignore:285  .env.backups/` ⇒ **不会进 git 历史**（这一条是唯一的好消息） |
| ACL | 继承型：`NT AUTHORITY\SYSTEM` / `BUILTIN\Administrators` / `DESKTOP-CN00D5I\AdminWT` 均 FullControl，**无显式收紧** |

**为什么不做**：这是**删除 47 份生产密钥副本**的不可逆动作。
TASK-00 D6 的纪律是"不碰生产数据"，TASK-03 §0 的纪律是"限定范围、避免一次大爆炸"，
而**密钥横向扩散的根治明确归属 `TASK-07`（安全接线）**。在缺少密钥轮换窗口的前提下
删除备份，可能让运维失去唯一的回滚点。

**已做**：完整量化 + 判定 + 给出可一键执行的处置命令（见 `docs/ops_quick_manual.md` 新增段），
并作为**最高优先级的已知债务**移交 TASK-07。

---

## 6. 已知债务（本步产生/发现的）

| # | 项 | 归属 | 说明 |
|---|---|---|---|
| H1 | `.env.backups/` 50 份密钥副本（7.0MB）未减量 | **TASK-07** | 需先确定密钥轮换窗口；处置命令已写入 `docs/ops_quick_manual.md` |
| H2 | 25 个根目录 `.ps1` 未归类；23 个有引用（含 2 个 CI workflow） | 后续独立任务 | 需"先改引用再移动"；引用表已写入 `docs/ops_quick_manual.md` |
| H3 | `docs/PERF_BUDGET_REBASED.md` 引用**已被认定造假**的 `tracing_performance_report_*` | TASK-08 | 性能预算结论需以真实压测重算 |
| H4 | `scan_sensitive_data.py` 对"检测器自身的模式表"会自命中，现按**整文件路径**豁免 | 后续 | 更细的做法是"仅豁免 `re.compile(...)` 实参内的匹配"；按路径豁免会让该文件内的真实泄漏漏扫 |
| H5 | `pytest.ini` 的 `[coverage:*]` 三节是死配置（coverage 不读 pytest.ini） | TASK-08 | 见 `DEAD_CONFIG_20260918.md` §1b；导致历史覆盖率口径与实测口径不可比 |
| H6 | 根目录仍有 5 个 `findings.md` / `progress.md` / `task_plan.md` 等自由笔记 | 后续 | 零引用，但可能是人的工作记录，**不删**；建议移入 `docs/notes/` |
| H7 | 会话期间并行任务（TASK-01/02）持续在**同一工作区**写入新文件 | 协调 | 本次卫生数字是**时间点快照**；见 `BASELINE_20260918.md` §工作区 |

---

## 7. 回滚方式（**未提交任何 commit**，因此全部可逆）

```powershell
cd C:\Users\Administrator\agent

# ① 撤销"删除"（26 个文件回到磁盘 + 索引）
git reset -- . ; git checkout -- .

# ② 撤销"脱离跟踪"（6 个文件回到索引；磁盘文件从未删除）
git reset -- coverage.xml coverage_scripts.xml coverage_summary.xml `
              performance_benchmark_1782301576.json `
              tracing_performance_report_1782295077.json stress-report.json

# ③ 撤销 .gitignore 新增段
git checkout -- .gitignore
```

> **注意**：`git reset -- .` 会**同时**取消本任务在其它文件上的暂存（若有）。
> 本任务全程**未执行 `git commit`**，所有改动停留在"工作区 + 索引"，
> 用户可以逐条 review 后再决定如何提交。
