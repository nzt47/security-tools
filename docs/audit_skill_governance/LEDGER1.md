# LEDGER-1 · 最后一条红收尾报告

- 卡号：**LEDGER-1**（收尾卡）
- HEAD：`5c9ace10`（分支未动；本卡**未做任何 git commit / git add / checkout**）
- 目标红：`tests/unit/test_s3_01_handover.py::TestL4StageIngestion::test_real_ledger_has_no_unstaged_asset`
- 结论：**判定 = (b) 本批（G1-B/M8 重建窗口内的 S1-02 重跑）新增 wire 了一条存量资产，但没跑 S3-01 首次入轨收口**
- 处置：**给 `cp.skill.skill` 补上 stage（`borrowed`，走 S3-01 L4 官方收口入口）** —— 不是放宽断言，**断言与测试文件一字未改**
- 环境：Python 3.12.0 系统解释器；探针全部写在仓库外 `C:\Users\Administrator\AppData\Local\Temp\ledger1\`

---

## ① 判定：这条红是 (a) / (b) / (c) 中的哪一个

### 判定结论

**(b)**，精确表述为：

> 资产 `skill`（主轨 `data/skills_mgmt.json` 内，2026-09-10 起就在，**本批之前**）一直没被 wire 进 descriptor 台账；
> **本批 G1-B/M8 重跑了 S1-02 回填**（`scripts/run_s1_02_backfill.py`），`run_backfill` 对「台账里没有的资产」执行 `register`，
> 于是在 `2026-09-26T01:01:27` **新建**了 `cp.skill.skill`（`stage=None` 是 S1-02 的设计，stage 归 S3-01 管），
> 但**没有人随后跑 S3-01 首次入轨**（`scripts/run_s3_01_ingest.py --execute`）⇒ L4 不变量被打破。

**不是 (a)（H-3 决策的直接后果）**，**不是 (c)（本批之前就存在）**。两边证据如下。

### 证据 1：红本身（原始输出，修复前）

```text
$ python -m pytest tests/unit/test_s3_01_handover.py -q
E   assert ['cp.skill.skill'] == []
E     Left contains one more item: 'cp.skill.skill'
...
FAILED tests/unit/test_s3_01_handover.py::TestL4StageIngestion::test_real_ledger_has_no_unstaged_asset
======================== 1 failed, 39 passed in 3.95s =========================
```

台账侧（修复前）：

```text
descriptors 条目 = 32，未入轨（evolution.stage 为空）= 1
  cp.skill.skill   created_at = 2026-09-26T01:01:27.361834   stage = None
其余 31 条：borrowed ×29（其 stage 由 2026-09-11/09-12 的 descriptor.stage 审计写入）、shadow ×2（cp.builtin.read_file / cp.builtin.write_file）
```

### 证据 2：这条条目是**本批窗口内新建**的（不是「原本就漏了 stage」）

台账自带审计环（`data/descriptors.json → "audit"`，163 条）里，`cp.skill.skill` 只有 3 条，第一条就是「创建」：

```json
{"ts": "2026-09-26T01:01:27.362836", "action": "descriptor.register", "capability_id": "cp.skill.skill",
 "actor": "backfill:s1-02", "detail": {"action": "created", "reason": "s1-02 存量资产字段回填（wire: skill）"}}
```

- 全台账 `descriptor.register` 共 42 条，按小时分布：`09-09T08: 29 条 / 09-10T12: 3 / 09-10T13: 6 / 09-10T18: 3 / 09-26T01: 1` —— **09-26 那 1 条就是它**。
- `detail.action == "created"` ⇒ 注册前台账里**不存在**该 capability。
- 台账内 `descriptor.stage` 审计共 30 条，日期集合 = `['2026-09-11', '2026-09-12']` —— **本批（09-26）从未清空过任何条目的 stage**（⇒ 不是「重建时把已有的 stage 弄丢了」）。

G1-B 报告（`G1B_REPORT.md:342`）记录本次重建的 sha 变化时间：**2026-09-26 01:03:33**（`25dda2f23e81a204… → 259507ecde269408…`），与 01:01:27 的 wire 事件同一窗口（G1-B 报告落盘 01:14:54）。

### 证据 3：被 wire 的资产 `skill` **在本批之前就在**（排除「本批新增资产」）

```text
main["skill"] = {"id":"skill","name":"易之三义","created_at":"2026-09-10T10:50:30.001166",
                 "updated_at":"2026-09-10T10:53:38.669022","status":"approved","enabled":true,
                 "source":"manual","author":"workbench","category":"custom","version":"0.1.0"}
```

- 主轨记录 **16 天未动**（updated_at = 2026-09-10）。
- `data/skills_mgmt.json` **不受 git 跟踪**（`git ls-files --error-unmatch` 报 pathspec 不匹配；最后触碰它的提交是 `91d32bbb 2026-07-19` 的「清理运行时数据跟踪」）⇒ 本批无法通过版本控制「新加」这条记录。
- 文献佐证：`G1B0_recheck.md:843`（2026-09-25 22:07，**本批 M8 之前**）已把 `skill` 列在「H-3 的 7 条主轨独有技能」里。

### 证据 4：**不含本批改动**的等价台账 = 绿（排除 (c)）

非破坏手段：把「09-26T01:01:27 新建的那 1 条」从台账副本里剔除（依据就是上面那条 `register/created` 审计），得到「改前」等价台账（31 条），再跑一次断言：

```text
=== E1 等价「改前」台账（剔除本批 register 新增的 cp.skill.skill）===
  entries = 31 | 未入轨 = []
  -> L4 断言在这份台账上: GREEN
  被剔除条目的 stage: None
  台账内 descriptor.stage 审计 = 30 条 | 日期集合 = ['2026-09-11', '2026-09-12']
```

⇒ 在本批的 wire 事件之前，这条断言是**绿的**；红的最早可能诞生时间就是 2026-09-26 01:01:27。
（旁证：最早报这条红的报告 `F11C.md` mtime = 2026-09-26 01:04:31，比 wire 事件晚 3 分钟；G1-C 09:26 复报。两者都在 wire 之后，**没有任何本批之前的红记录**。）

### 证据 5：**不含本批改动**的代码也会造出这条红（把「本批代码引入」彻底排除）

本批对 `agent/descriptors` 的**唯一**改动是 M8 的描述取源（`git diff HEAD --stat`）：

```text
 agent/descriptors/backfill.py | 21 ++++++++++++++++++---
 1 file changed, 18 insertions(+), 3 deletions(-)
```

把 `git show HEAD:agent/descriptors/backfill.py`（**HEAD 版，不含本批任何改动**，65,816 B）装进临时包，用**同一份今天的数据**、针对**同一份「改前」等价台账**重跑 S1-02：

```text
=== E2 HEAD 版 backfill.py（不含本批改动）===
  HEAD 版字节数 = 65816 | 含本批 M8 标记 = False | 含 wire-on-register = True
  [HEAD] assets_total = 30 | applied.register = 1
  [HEAD] 重跑后 cp.skill.skill 存在 = True | stage = None | source_type = skill | provenance = declared
  [HEAD] 重跑后台账条目 = 32 | 未入轨 = ['cp.skill.skill']
  [HEAD] register 审计 = {"ts": "2026-09-26T15:21:47.487022", "action": "descriptor.register",
        "capability_id": "cp.skill.skill", "actor": "backfill:s1-02",
        "detail": {"action": "created", "reason": "s1-02 存量资产字段回填（wire: skill）"}}
```

⇒ **HEAD 代码 + 改前台账 + 今天的数据 = 逐字复现同一条红**。所以红不是本批**代码**引入的，而是本批**执行了重建**（重跑 S1-02）把一条**潜伏已久的 wire 缺口**兑现了。

### 证据 6：为什么**不是 (a) H-3 的直接后果**

1. **H-3 管的是另一条轨**：H-3 = 「7 条主轨独有技能里 5 条纳入唯一事实源、`global-core-principles` 与 `skill` **不纳入**」（`G1B0_recheck.md:843`、`G1C.md:317-318 / 842`）。它的落点是**清单/检索事实源域**，本卡涉及的是 **descriptor 台账的 evolution.stage**。
2. **H-3 的「另一半」自己在台账里就有 stage**：`cp.skill.global-core-principles`（与 `skill` 同被 H-3 排除）在**同一份台账**里 `stage=borrowed`，自 2026-09-11 起。⇒ `stage=None` **不是** H-3 的实现方式。
3. **「没有 skill.md ⇒ 没有入轨来源」不成立**：入轨判定只读 descriptor 自身 —— `agent/digestion/stage.py:654-667 first_entry_stage()` 只看 `desc.origin.source_type`，**从不读文件轨**；`backfill_stages()` 枚举的是 `reg.list()`（台账条目），与 `data/skills_repo/<id>/skill.md` 是否存在**无关**。
4. **`runtime_only` 是派生的、且在另一条轨**：`agent/lines/callability.py:1193-1221 runtime_only_skill_entries()` 的 docstring 明写：id=`skill` 的技能「内容内联在 `data/skills_mgmt.json`、仓库里没有 `data/skills_repo/skill/skill.md` 实体」，该函数**只服务 REST 端点的 `scope=runtime` 补位**，并且「**不参与** `data/capability_manifest.json` 的生成」。它由「声明 − 仓库实体」派生，与 descriptor 台账无关。
5. **H-3 的 P0 约束本卡没碰**：`G1C.md:466`「保住『不纳入』这个结论，**别为它建 skill.md**」。本卡**没有**创建 `data/skills_repo/skill/`：

   ```text
   H-3 P0 校验：data/skills_repo/skill 仍不存在 = True
   ```

   H-3 的清单轨也没动：修复后 `python scripts/sync_capability_manifest.py --check` → `[OK] 清单与权威数据一致：119 条能力（location: local 98 / remote 21）`，**EXIT=0**（与修复前逐字一致）。

### 我做不到的部分（诚实登记）

- `data/descriptors.json` 是 **gitignored 的运行时产物**（`.gitignore:226`），仓库里**没有任何 M8 之前的副本**（`data/` 下 `*descriptor*` 只有当前这一个；`data/digestion/` 也没有 09-26 之前的 ingest 备份）。所以「改前台账」是**语义重建**（剔除 `register/created` 审计指向的那一条），**不是逐字还原**。若 09-26 之前存在「某条无 stage 条目后来被别的手段修好」的情形，这种重建看不出来；但该可能性被证据 2（30 条 stage 审计全在 09-11/09-12、09-26 无 stage 变更）与 G1-B 记录的 sha 变化时间共同压低。
- 小对账差异（不影响判定）：`G1B_REPORT.md:384 (D-5)` 写「descriptors.json 里 **29 条** skill 记录」，而今台账 `cp.skill.*` 有 **30 条**（含 01:01:27 新增的 `skill`）。如实登记为**文档口径小差异**。

---

## ② 处置选择与理由

### 选择：**补 stage**（不是「标注为非资产」），走官方收口入口

```text
python scripts/run_s3_01_ingest.py --dry-run     # 干跑先看计划
python scripts/run_s3_01_ingest.py --execute     # 实跑（自动备份改前台账）
```

结果：`cp.skill.skill` → `evolution.stage = "borrowed"`（七态主轨**最低证据态**），`trace_policy` 同时切到真实台账引用。

### 为什么不选「显式标注为非资产/运行时项」

1. **台账 schema 里没有「非资产」这个概念，`None` 的语义就是「未完工」**：`agent/descriptors/validator.py:152-153` 把 `evolution.stage is None` 直接判为 **warning**：`evolution.stage 未入轨（None=缺证据待 S3 补验，S0-02 §3.4）`。本次入轨报告亦印证：`warnings_before=1 → warnings_after=0`。**没有任何字段能把「运行时项」表达为一种合法的 stage 缺省**；要「显式化」就得改 schema/模型，那才是真正动契约。
2. **descriptor 台账本来就把它当资产**：S1-02 的职责是「存量技能资产全覆盖」，它在 09-09 就把主轨独有的 `global-core-principles` wire 了进来；本批重跑时 wire 的 `skill` 与它是同一类。凭 H-3（清单/检索轨的「不纳入」）反推「descriptor 台账里也不算资产」，**没有权威数据源支撑**。
3. **权威派生源不存在于 descriptor 层**：唯一可称权威的 `runtime_only` 派生在 `agent/lines/callability.py`（「声明 − 仓库实体」），它明确**不参与** capability 清单生成、只服务 REST 补位。让 descriptor 层消费它，会引入 `descriptors → lines` 的反向依赖（`backfill.py` 头部「零依赖叶子」纪律恰恰禁止），并且**必须改测试断言**。

### 为什么这不是「放宽断言」

- `tests/unit/test_s3_01_handover.py` **一字未改**（`git diff HEAD -- tests/unit/test_s3_01_handover.py` 输出为空）；`assert empty == []` 原样保留，没有排除名单、没有 skip、没有 white-list。
- 红是**在数据层被修好的**：让台账**真的**满足不变量（32/32 有 stage），而不是让断言**绕过**不满足的那一条。
- 断言守的「没有资产被静默丢弃」反而**变强了**：原本那条新 wire 的资产处在「已登记但未入轨」的静默状态（正是断言要抓的），现在它有了 stage、审计链上有 `descriptor.stage` 记录、并发出了 `digest.stage` 事件。
- H-3 无冲突（见 ① 证据 6）：不建 skill.md、不动清单（`--check` 仍 EXIT=0 / 119 条）、同被排除的孪生条目本来就带 stage。

---

## ③ 改动 diff

### 文件清单

| 文件 | 类型 | 受 git 跟踪 | 改动 |
|---|---|---|---|
| `data/descriptors.json` | **运行时台账**（`.gitignore:226`） | 否 | 1 条条目的 `evolution`（+ 台账内审计环 +1 条） |
| `data/audit/audit_chain.db`（+ `.lock`/`.seqjournal`） | 运行时审计链 | 否 | **+1 条**（seq 72701） |
| `data/events/events.jsonl`（+ `.lock`） | 运行时事件流 | 否 | **+1 条** `digest.stage`（43,024 → 43,531 B） |
| `data/digestion/descriptors_pre_ingest_20260926_152234.json` | 改前台账备份（工具自动） | 否 | 新增（156,885 B） |
| `data/digestion/descriptors_pre_ingest_20260926_152301.json` | 幂等复跑的重复备份 | 否 | 新增（157,518 B，冗余，可删） |
| `docs/audit_skill_governance/LEDGER1.md` | 本报告 | 目录整体未跟踪 | 新增 |
| `agent/descriptors/**`、`tests/unit/test_s3_01_handover.py`、`scripts/**`、`config.yaml` | —— | —— | **零改动**（`git diff HEAD` 只显示本批既有的 M8 `backfill.py` 改动，非本卡所加） |

### 台账语义 diff（`cp.skill.skill`）

```diff
 "cp.skill.skill": {
   "meta": { "created_at": "2026-09-26T01:01:27.361834",
-            "updated_at": "2026-09-26T01:01:27.444464" },
+            "updated_at": "2026-09-26T15:22:34.657280" },
   "evolution": {
-    "stage": null,
+    "stage": "borrowed",
     "internalize_attempts": 0,
     "shadow_config": {},
-    "trace_policy": ""
+    "trace_policy": "trace:skill-import:import-ledger:ledger=unified_traces@agent/data/tool_trace.db#capability_id=cp.skill.skill#read=UnifiedTraceStore.list_by_capability"
   }
 }
```

其余 31 条条目**逐字未动**（台账 sha 变化仅来自这 1 条 + 台账内审计环追加 1 条 `descriptor.stage`）。

---

## ④ 验证结果

### 4.1 目标用例

```text
$ python -m pytest tests/unit/test_s3_01_handover.py -q
============================= 40 passed in 3.37s ==============================
```
（修复前：`1 failed, 39 passed in 3.95s`）

### 4.2 审计链（逐项核对）

| 项 | 修复前 | 修复后 | 判定 |
|---|---|---|---|
| `data/audit/audit_chain.db` sha256 | `2ee6ef46841bf235f16a382f2eee6b0386af654bd66dba419b20b6a360d39768` | `16272b29ab728fcfb8138e77144010342c7ec842c13f53a07967ed26b5dd18b3` | **变了**（见下） |
| 链行数 / max_seq | 72,700 / 72,700 | 72,701 / 72,701 | **+1 条** |
| 重复 seq / 断号 | 0 / 0 | 0 / 0 | **无缺口、无重复** |
| 新增条目 | — | `seq=72701, ts=2026-09-26T07:22:34.733280+00:00, actor=digestion_pipeline, action=descriptor.stage, subject=capability:cp.skill.skill` | 与入轨报告 `audit_seq=72701` 一致 |
| 链完整性 | — | 新增条 `prev_hash = e5de097e…c65ac9` **== 上一条(72700) 的 `self_hash`** | **链接连续，未破链** |
| `data/audit/daily_roots.jsonl` sha256 | `04991152919985fc21ec3becd877a9d0f04b82cc3fe0fc70ee40389c82dc2832` | `04991152919985fc21ec3becd877a9d0f04b82cc3fe0fc70ee40389c82dc2832` | **不变 ✓** |

**关于「审计链 sha 不变」**：`daily_roots.jsonl` sha256 **完全不变 ✓**。`audit_chain.db` sha **确实变了**，且这正是任务书预先认可的「台账重建可能追加审计链 —— 审计链追加是预期的」：`registry._audit()`（`registry.py:457-469`）对每个 `descriptor.*` 动作都会 best-effort 同步写全局链。**追加量 = 恰好 1 条**（`descriptor.stage`），seq 无缺口无重复、链哈希连续。若不追加，反而意味着审计轨缺了这一笔治理动作。

### 4.3 幂等（连跑两次 sha256 相同）

```text
入轨后 sha256            = 325ac2d14a692007c92c3f99e3b4a8fa75641e318ad181eeb727daa3022fdede
第 1 次重跑 exit=0 | 入轨成功=0 失败=0 残留未入轨=0
  重跑 1 后 sha256        = 325ac2d14a692007c92c3f99e3b4a8fa75641e318ad181eeb727daa3022fdede | 与首次相同 = True
第 2 次重跑 exit=0 | 入轨成功=0 失败=0 残留未入轨=0
  重跑 2 后 sha256        = 325ac2d14a692007c92c3f99e3b4a8fa75641e318ad181eeb727daa3022fdede | 与首次相同 = True
最终 未入轨 = [] | 条目 = 32
```

**上游重建步同样幂等**（对台账**字节副本**跑 S1-02 实跑，避免动真台账）：

```text
真实台账 sha = 325ac2d14a692007c92c3f99e3b4a8fa75641e318ad181eeb727daa3022fdede
探针副本 sha = 325ac2d14a692007c92c3f99e3b4a8fa75641e318ad181eeb727daa3022fdede | 相同 = True
第 1 次重跑 S1-02 重建 exit=0 | sha=325ac2d1… | 与真实台账相同 = True   （deterministic=true）
第 2 次重跑 S1-02 重建 exit=0 | sha=325ac2d1… | 与真实台账相同 = True   （deterministic=true）
```

⇒ 当前台账是「**S1-02 重建 → S3-01 入轨**」这条流水线的**不动点**：连跑两次 sha256 相同，且重建步本身对今天的存量数据是 no-op。

### 4.4 回归（未放宽任何断言）

| 批次 | 文件数 | 结果 |
|---|---|---|
| descriptor/digestion 主集（`test_s3_01_handover`、`test_descriptors_*`×5、`test_digestion_*`×17、`test_skill_h3_migration`、`test_s4_01_stage_promote_chain`、`test_capregistry_*`×4、`test_confirm_level`、`test_s6_01_ui_panels`、`test_tool_exemptions`、`test_trace_v2`、`test_trace_v2_integration`、`test_audit_chain`、`test_audit_daily_root_consistency`、`test_audit_facade`） | 36 | `1850 passed, 4 skipped, 0 failed`（107.66s；skip 全为 `需要 --runslow`） |
| 扩集（所有 import `agent.descriptors`/`agent.digestion` 且未含在上面的用例：`test_isolation_*`×4、`test_judge_*`×2、`test_memory_forgetting`、`test_s10_02_judge_wiring`、`test_s11_04_judge_master_switch`、`test_s5_03_cost_brake`、`test_s7_05_real_data`、`test_s7_06_*`×2、`test_saga`、`test_security_actor_matrix`、`test_skill_description_single_source`、`test_audit_integration`） | 17 | `908 passed, 5 skipped, 0 failed`（55.54s；skip 全为本机环境如实差距：无 `resource` 模块 / Docker daemon 未运行） |
| **合计** | **53** | **2758 passed / 0 failed / 9 skipped** |

另两项硬约束复核：

```text
$ python scripts/sync_capability_manifest.py --check
[OK] 清单与权威数据一致：119 条能力（location: local 98 / remote 21）
EXIT=0
```
（修复前后**逐字一致**，未把清单弄成不同源。）

```text
H-3 P0 校验：data/skills_repo/skill 仍不存在 = True
```

---

## ⑤ 未验证项与残留风险

**未验证**

1. **未重跑全量 `tests/unit`（22,633 条）**。本卡只跑了与 descriptor/digestion/台账读取相关的 **53 个用例文件（2758 passed / 0 failed）**；「全量无新红」由发起方复跑确认更稳妥。
2. `trace_policy` 只验证了**形状**（非占位串、含 `ledger=unified_traces`，`test_real_ledger_policies_have_no_placeholder` 通过），**没有**对活的 `UnifiedTraceStore` 做端到端回指验证（需要真实 trace，属 S7 探针卡范围）。
3. 本批之前的台账**字节级**不可复得（无历史副本，见 ① 末），「改前」为语义重建。
4. 本卡未执行任何 git 写操作，故**无法**用 git 反证运行时文件基线；台账基线由本卡自录 sha256（`9718d916…`）锚定。

**残留风险**

1. **复发风险（中）**：收口动作**不在代码里**。`run_backfill` 会 wire 台账里缺失的资产，但**不会**顺手做 S3-01 首次入轨；`backfill_stages` 也只在被显式调用时运行。⇒ **今后任何一次「主轨新增技能 + 重跑 S1-02」都会重新制造同一条红**。建议后续小卡二选一：(i) 把 `run_s3_01_ingest.py --execute` 写进重建 runbook（并在 `run_s1_02_backfill.py` 末尾打印提醒）；(ii) 让 `run_backfill` 的返回值显式列出「本次新 wire 的 capability_id + 待跑 S3-01 收口」（纯新增字段，不改行为）。**本卡刻意不做代码改动**（收尾卡 + 43 张卡在途，改共享模块的收益不抵风险）。
2. **链已 +1 条不可撤回**：审计链 append-only，回滚台账**不会**撤销 seq 72701。若要求「链与台账同态」，只能再补一条 `descriptor.stage(None)` 的反向迁移（会再 +1 条并触发 validator warning）—— **不推荐**。
3. **重复备份**：`descriptors_pre_ingest_20260926_152301.json`（sha 等于修复后状态）是幂等复跑时 CLI 自动产生的冗余备份，可安全删除。
4. **文档小差异**：`G1B_REPORT.md:384` 的「29 条 skill 记录」与今台账 30 条 `cp.skill.*` 不一致（见 ① 末），不影响本判定。

---

## ⑥ 回滚指令

**唯一改动落在运行时台账上（gitignored），所以回滚 = 覆盖回备份文件**（备份 sha 已核对为改前逐字值）：

```powershell
# 1) 用改前备份逐字还原台账（已核对 sha256 = 9718d9161e65fe630015839721c6d544fab2e38474a8d6b35623492daa99cc6d）
Copy-Item 'C:\Users\Administrator\agent\data\digestion\descriptors_pre_ingest_20260926_152234.json' 'C:\Users\Administrator\agent\data\descriptors.json' -Force
(Get-FileHash 'C:\Users\Administrator\agent\data\descriptors.json' -Algorithm SHA256).Hash
# 期望：9718D9161E65FE630015839721C6D544FAB2E38474A8D6B35623492DAA99CC6D

# 2) 回滚后该用例会重新变红（预期：红在数据层）
#    python -m pytest tests/unit/test_s3_01_handover.py -q   ->  1 failed, 39 passed

# 3) 若要再次恢复修复态：重跑官方收口入口（幂等）
#    python scripts/run_s3_01_ingest.py --execute
```

**回滚不覆盖的部分（必须知情）**：`data/audit/audit_chain.db` 的 seq 72701 与 `data/events/events.jsonl` 的那条 `digest.stage` 事件**保留**（审计链 append-only，禁止改写；这正是「回滚不抹痕迹」的设计）。**不需要**、也**不要**用 `git checkout`/`git stash` 回滚（本卡未改任何受跟踪文件）。

---

## ⑦ 残留物自证

### 7.1 本卡时间窗（15:20 之后）仓库内**全部** mtime 变更文件

```text
rel                                                          Length LastWriteTime
data\audit\audit_chain.db                                  55177216 2026/9/26 15:22:34
data\audit\audit_chain.db.lock                                 1025 2026/9/26 15:22:34
data\audit\audit_chain.db.seqjournal                        1724657 2026/9/26 15:22:34
data\descriptors.json                                        157518 2026/9/26 15:22:34
data\digestion\descriptors_pre_ingest_20260926_152301.json   157518 2026/9/26 15:22:34
data\events\events.jsonl                                      43531 2026/9/26 15:22:34
data\events\events.jsonl.lock                                   513 2026/9/26 15:22:34
```
（扫描已排除 `__pycache__` / `.pytest_cache` / `test_reports` / `cache` / `.git`。第 2 个备份 `…_152234.json` 未出现在此表，是因为 `shutil.copy2` 保留了源文件的 mtime=07:47:14，它本身在 7.2 列出。）

⇒ **除上表 7 项外，仓库内没有任何文件被本卡写入**；特别核对：`data/capability_manifest.json` 的 mtime **早于**本卡窗口（不在表中）⇒ 本卡**未触碰**清单文件（`--check` 仍 EXIT=0）。

### 7.2 本卡新增的运行时产物（全部 gitignored）

| 路径 | 说明 | 建议 |
|---|---|---|
| `data/descriptors.json` | 修复后的台账（32 条，未入轨 0） | 保留（修复本体） |
| `data/digestion/descriptors_pre_ingest_20260926_152234.json` | **改前**台账，sha `9718d916…` | **保留**（回滚锚点） |
| `data/digestion/descriptors_pre_ingest_20260926_152301.json` | 幂等复跑产生的重复备份，sha `325ac2d1…` | 可删 |
| `data/audit/audit_chain.db`(+lock/seqjournal) | +1 条链记录（seq 72701） | 保留（append-only） |
| `data/events/events.jsonl`(+lock) | +1 条 `digest.stage` 事件（`ev_5f886042b9f1c6541c9415aacef9ded7`） | 保留 |
| `docs/audit_skill_governance/LEDGER1.md` | 本报告 | 保留 |

### 7.3 仓库外探针（**不在仓库内**，可整目录删除）

`C:\Users\Administrator\AppData\Local\Temp\ledger1\`：`probe1..6.py`、`baseline.py`、`exp.py`、`exp2.py`、`idem.py`、`rebuild_idem.py`、`state_before.py`、`state_after.py`、`chain_probe.py`、`report_peek.py`、`eq_pre.json`、`eq_pre_head_after.json`、`rebuild_probe.json`、`headpkg\`（HEAD 版 backfill.py 临时包）、`ingest_*_report.json`、`rerun*.json`、`s102_out\`、`before.json`。

### 7.4 禁止项自检

- 未 `git commit` / `git add` / `git checkout` / `git stash`（本卡对受跟踪文件**零改动**）。
- 未触碰：`agent/skills_mgmt/`、`agent/tool_router*.py`、`agent/audit/`、`plugins/`、`yunshu-ui/`、`config.yaml`、prompt 装配四件套、`data/skills_repo/`、`data/audit/daily_roots.jsonl`、`data/learned_workflows.json`、`scripts/sync_capability_manifest.py`。
- 未 `taskkill` 任何 python 进程；未起常驻服务；未使用 `venv/`（系统 Python 3.12.0）。
- 未出网（零网络访问）。
