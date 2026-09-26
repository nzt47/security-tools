# RUNBOOK-1 · S1-02 重建 → S3-01 收口：把"再制造同一条红"钉进代码

- 卡号：**RUNBOOK-1**（小卡）
- HEAD：`5c9ace10`（分支未动；本卡**未做任何 git commit / git add / git checkout**）
- 承接：`docs/audit_skill_governance/LEDGER1.md` §5「复发风险（中）：**收口动作不在代码里**」
- 结论：**方案 A（推荐）+ 方案 B（最小 runbook）都做了**；A 为主 —— 流水线自己报出待收口清单，
  并在同一次调用里幂等地自动收口；另加一条**不动点护栏测试**，让"再次制造同一条红"在代码层被抓住。
- 环境：Python 3.12.0 系统解释器（未用 `venv/`）；探针全部写在仓库外
  `C:\Users\Administrator\AppData\Local\Temp\runbook1\`；**零出网**。

---

## ① 选了哪个方案 + 论证

### 选择：**A（代码：加法式字段 + 可关闭的自动收口）+ B（补一份最小 runbook）**

| | 做了什么 | 为什么 |
|---|---|---|
| **A-1** | `run_backfill` 返回值**新增** `newly_wired`（本次 wire 的 capability_id 列表）与 `s3_01_followup`（待收口清单 + 官方处置命令 + 是否已自动收口） | LEDGER-1 的建议 (ii)：「让 run_backfill 返回『新 wire 的 capability_id + 待跑 S3-01 收口』」—— **纯新增字段**，任何既有调用方都能看到待办，而不是只在台账里留一个沉默的 `stage=None` |
| **A-2** | `run_backfill(..., ingest_stages=False)` 新增形参；**CLI `run_s1_02_backfill.py` 实跑默认置 True**（`--no-ingest-stages` 可关） | LEDGER-1 的建议 (i) 的**代码化**：「把 `run_s3_01_ingest.py --execute` 写进重建流程」—— 与其让人记得跑 runbook，不如让**重建这一次调用**就走到不动点 |
| **B** | 新增 `docs/audit_skill_governance/LEDGER_REBUILD_RUNBOOK.md`（最小，93 行） | 仓库里**没有**活的重建 runbook：`docs/zh/CloudPivot_v7.2重构计划/S1-02_交付结案报告_20260909.md` 与 `S3-01_交付结案报告_20260911.md` 都是**归档结案报告**（带固定 commit 号的历史文件），往里追加会篡改归档；故按任务书"若没有就新建一份最小的"执行 |
| **护栏** | `tests/unit/test_s1_02_s3_01_fixpoint_guard.py`（4 例，tmp 台账） | 见 ③ —— 这是本卡**唯一能自动发现**"流水线又不断闭环"的手段 |

### 为什么 A-1 必须是加法式（论证与证据）

`run_backfill` 的返回结构至少有三个既有消费方：

1. `scripts/run_s1_02_backfill.py`：`run["pending"]["needs_review"]`、`run["pending"]["needs_undo_hint"]`、
   `run["validation"]`、`run["applied"]`、`run["batches"]`、`run["rollback_events"]`，
   并整包落盘 `data/descriptors_s1_02/run_latest.json`；
2. `agent/descriptors/backfill.py::full_backfill`（第 1288 行）；
3. `tests/unit/test_descriptors_backfill.py`（5 处调用：`run["applied"]["no_op"]`、`run["batches"]`、
   `run["validation"]`、`len(run["rollback_events"])` …，含 `test_rerun_idempotent`、
   `test_dry_run_identical_and_non_mutating` 的**全字典 json 相等**断言）。

⇒ 本卡**没有改动任何既有字段的类型/语义**，只追加了两个键与一个**缺省 False** 的形参。

### 为什么"自动收口"不会破坏既有调用方与性能（论证）

1. **库层默认关**：`ingest_stages=False` ⇒ 上面三个消费方**零行为变化**；
   实测 `test_descriptors_backfill.py` 与 `test_s3_01_handover.py` 在改动前后同为全绿（见 ④）。
2. **只在 CLI 层默认开**：`run_s1_02_backfill.py` 是重建的**唯一入口**，它**本来就写台账**
   （写 register/provenance/trust）；"写了 wire 却不写 stage"是缺陷而不是契约。
   要旧行为：`--no-ingest-stages`（此时脚本仍打印待收口清单与处置命令，不静默）。
3. **幂等 + 对不动点逐字节 no-op**：收口调用的就是官方入口的同一个函数
   `backfill_stages(reg, execute=True)`（actor 同为 `digestion_pipeline`）；本卡只在其**确有写入**
   （`ingested` 非空或 `trace_policy` 占位串被切换）时才 `reg.save()`。
   实测（⑤ 探针 A）：在**当前真实台账副本**上自动收口 ⇒ 入轨 0 条、台账 sha **完全不变**。
4. **干跑 / 批失败绝不自动收口**：`auto = ingest_stages and not dry_run and not stopped`。
5. **性能**：`backfill_stages` 是 `reg.list()` 两趟 O(n) 遍历 + **仅对 stage=None 的条目**各写一次审计；
   本仓 n=32，实测整条 CLI（含摸底、两次干跑一致性校验、分批回填、收口、全量重校验）**数秒级**（⑤ 有原始输出）。
6. **架构纪律**：`agent/digestion/stage.py` 只在**函数内懒加载**（`s3_01_followup()` 内部），
   与既有的 `_resolution_helpers()` 同一模式 ⇒ `agent/descriptors` **导入期仍是纯依赖叶子**，不引入反向依赖。

---

## ② 改动 diff（**加法式**证据）

| 文件 | 变化 | 性质 |
|---|---|---|
| `agent/descriptors/backfill.py` | **+138 / -2 行**（8 个 hunk） | 纯加法；**-2 见下** |
| `scripts/run_s1_02_backfill.py` | 153 → **190 行**（+37） | 纯加法（新形参 + 新打印块 + docstring） |
| `tests/unit/test_s1_02_s3_01_fixpoint_guard.py` | 新增（4 例） | 本卡护栏 |
| `docs/audit_skill_governance/LEDGER_REBUILD_RUNBOOK.md` | 新增（93 行） | 最小 runbook |
| `docs/audit_skill_governance/RUNBOOK1.md` | 新增 | 本报告 |

**"加法式"的最硬证据**：把本卡改动逐条反向摘掉后，与 `git show HEAD` 的差异**恰好等于**在途卡
M8 的那一处改动（`18 insertions(+), 3 deletions(-)`，与 LEDGER-1 §① 证据 5 记录的 M8 口径**逐字一致**）：

```text
$ git diff --no-index --stat backfill_HEAD.py backfill_without_rb1.py
 1 file changed, 18 insertions(+), 3 deletions(-)          # = 本卡之前的既有改动（M8）
$ git diff --stat HEAD -- agent/descriptors/backfill.py
 1 file changed, 156 insertions(+), 5 deletions(-)         # = M8(18/3) + 本卡(138/2) ⇒ 相加吻合
```

**那 2 个 `-` 是什么**（两行都是"被扩展的原行"，不是语义删除）：

```diff
-                counters: Dict[str, int]) -> List[str]:
+                counters: Dict[str, int],
+                wired: Optional[List[Dict[str, Any]]] = None) -> List[str]:

-                                   dry_run=dry_run, counters=result["applied"])
+                                   dry_run=dry_run, counters=result["applied"],
+                                   wired=wired)
```

**新增的对外契约（全部为新键/新形参）**

```diff
 def run_backfill(
     planned, registry=None, *,
     dry_run=False, actor=BACKFILL_ACTOR_PREFIX,
     batch_size=DEFAULT_BATCH_SIZE, registry_path=None,
+    ingest_stages: bool = False,
 ) -> Dict[str, Any]:
     result = { ...,
+        "newly_wired": [],          # 本次 register 桥接的资产明细
     }
     ...
+    result["s3_01_followup"] = s3_01_followup(reg, wired,
+        auto=bool(ingest_stages) and not dry_run and not result["stopped"],
+        dry_run=bool(dry_run))
     result["validation"] = validate_registry(reg)
```

`s3_01_followup` 的字段（**均为新键**）：
`required` / `newly_wired` / `stage_empty` / `auto_executed` / `ingested` / `failed` /
`command`（= `python scripts/run_s3_01_ingest.py --execute`）/ `reason`（人话 + 点名那条红）；
收口失败时额外带 `error`（**异常不外抛**，绝不吞掉回填结果）。

**顺序上的一个有意选择（[不易]）**：`s3_01_followup` 在 `validate_registry` **之前**调用。
否则自动收口清掉的 `stage 未入轨` warning 会在 `validation` 里以"收口前"的旧数字出现
（实测 CLI 输出 run1 会是 `warnings=5` 而非收口后的 `4`）—— 与调用结束时的台账状态不符。
缺省路径（`ingest_stages=False`）无写入 ⇒ 与从前逐字一致。

**未触碰（硬约束自检）**：`agent/skills_mgmt/**`（RET-1）、`tests/unit/conftest.py` 与测试卫生文件（TESTHYG-1）、
`agent/tool_router*.py`、`agent/audit/**`、`plugins/**`、`yunshu-ui/**`、`config.yaml`、
`data/audit/daily_roots.jsonl`、`data/learned_workflows.json`、`data/descriptors.json`（**一次都没读写**）。

---

## ③ 护栏测试 + 非空转自证

### 3.1 护栏：`tests/unit/test_s1_02_s3_01_fixpoint_guard.py`（4 例，全在 tmp 台账上）

```text
$ python -m pytest tests/unit/test_s1_02_s3_01_fixpoint_guard.py -q -p no:randomly
4 passed in 2.75s

$ python -m pytest tests/unit/test_s1_02_s3_01_fixpoint_guard.py tests/unit/test_s3_01_handover.py -q -p no:randomly
44 passed in 4.19s          # 4（本卡护栏）+ 40（L4 那条红所在的文件，基线 40）
```

| 用例 | 断言（原文） | 守什么 |
|---|---|---|
| `test_rebuild_ingest_rebuild_is_bytewise_fixpoint` | `assert _sha256(ledger) == fixed, "「重建 → 入轨 → 再重建」不是不动点：台账字节被改写"`；`assert _stage_empty(ledger) == [], "收口后台账仍有无 stage 资产"` | **不动点**（连跑 3 次 sha 恒定）+ **无 `stage=None`** |
| `test_default_call_does_not_ingest_but_reports` | `assert fw["required"] is True`；`assert fw["stage_empty"] == ["cp.skill.alpha-local", "cp.skill.skill"]`；`assert INGEST_CMD in fw["reason"]` | 缺省调用**零行为变化**但**必须提示**；并断言"不跑收口 ⇒ 台账真的残留未入轨" |
| `test_dry_run_reports_but_never_writes` | `assert not ledger.exists(), "干跑不得落库（台账文件被创建）"`；`assert fw["auto_executed"] is False, "干跑绝不自动收口"` | 干跑语义 + 新增字段不破坏干跑确定性 |
| `test_guard_has_teeth_when_pipeline_stops_closing_the_loop` | `with pytest.raises(AssertionError): assert _stage_empty(ledger) == [], "收口后台账仍有无 stage 资产"` | **变异自证**：把收口打成空转 ⇒ 护栏核心断言必红 |

**它怎么防"再次制造同一条红"**：这条红 = 「台账里有资产 `stage=None`」。
护栏在 tmp 台账上把整条流水线跑一遍，把这个不变量**每一轮都断言一次**，并额外要求
"再重建后台账逐字节不变"。于是只要有人再次把收口摘出流水线（或让它变成非幂等），
**CI 就会红在代码层**，而不是三周后由 `test_s3_01_handover` 在生产台账上迟报。

**为什么不碰生产台账**：全部走 `tmp_path` 下的迷你主轨 + 台账副本；
自动收口的 `digest.stage` 事件经 `CP_EVENTS_DIR` 关进 tmp；审计链在用例内**显式 bind**
一份 tmp `AuditChain`（不依赖 `tests/unit/conftest.py` 的会话级守卫是否在位 ——
该文件正由 TESTHYG-1 在途修改）。

### 3.2 非空转自证（去掉改动 ⇒ 护栏必红）

手段：把本卡改动**逐条反向摘掉**（文本级反向替换 + 断言零残留），跑护栏，再**逐字还原**并核对 sha256。
**未使用** `git checkout` / `git stash`（工作区有 44 张卡的改动）。

```text
$ python revert_probe.py drop
backup sha = 9de403efbbbb3102988a8f63f5dc960efcd5353f4a85fd44f74c1604097783b7   # 本卡改动后
dropped sha = facc9a031474a5e2464b2e8718da73d0340e66a33080284fc17963993ed32dc4   # 摘掉后（= HEAD + M8）

$ python -m pytest tests/unit/test_s1_02_s3_01_fixpoint_guard.py -q -p no:randomly --tb=line --no-header
collected 4 items
E   TypeError: run_backfill() got an unexpected keyword argument 'ingest_stages'
C:\...\test_s1_02_s3_01_fixpoint_guard.py:107: TypeError: run_backfill() got an unexpected keyword argument 'ingest_stages'
E   KeyError: 's3_01_followup'
C:\...\test_s1_02_s3_01_fixpoint_guard.py:159: KeyError: 's3_01_followup'
E   TypeError: run_backfill() got an unexpected keyword argument 'ingest_stages'
E   TypeError: run_backfill() got an unexpected keyword argument 'ingest_stages'
FAILED tests/unit/test_s1_02_s3_01_fixpoint_guard.py::TestPipelineFixpoint::test_rebuild_ingest_rebuild_is_bytewise_fixpoint
FAILED tests/unit/test_s1_02_s3_01_fixpoint_guard.py::TestPipelineFixpoint::test_default_call_does_not_ingest_but_reports
FAILED tests/unit/test_s1_02_s3_01_fixpoint_guard.py::TestPipelineFixpoint::test_dry_run_reports_but_never_writes
FAILED tests/unit/test_s1_02_s3_01_fixpoint_guard.py::TestPipelineFixpoint::test_guard_has_teeth_when_pipeline_stops_closing_the_loop
============================== 4 failed in 1.64s ==============================

$ python revert_probe.py restore
restored sha = 9de403efbbbb3102988a8f63f5dc960efcd5353f4a85fd44f74c1604097783b7   # 与 drop 前逐字相同 ✓
```

**诚实说明这条自证的强度**：摘掉改动后 4 例红在 **API 面**（`TypeError`/`KeyError`），
而不是红在那句 sha 断言上 —— 因为新字段/新形参不存在时根本走不到断言。
断言本身的"有牙"由第 4 例（变异自证）单独证明：把 `backfill_stages` 换成空转实现后，
`assert _stage_empty(ledger) == []` **确实不成立**，即"流水线不闭环 ⇒ 台账必残留 `stage=None`"
这一物理事实在 tmp 台账上被显式断言（第 2 例同样断言了 `stage_empty == [两条]` 这一非空清单）。

---

## ④ 回归结果（**未放宽任何断言**）

```text
$ python -m pytest tests/unit/test_s3_01_handover.py -q -p no:randomly
40 passed in 4.32s                       # 与卡上给的 40 passed 基线**逐字一致**

$ python -m pytest tests/unit/test_descriptors_*.py -q -p no:randomly     # 5 个文件
179 passed in 7.48s

$ python -m pytest tests/unit/test_digestion_*.py -q -p no:randomly      # 16 个文件
931 passed in 52.94s

$ 三者 + 本卡护栏合跑
1154 passed in 63.32s                    # 1150 + 本卡 4 例

# 复跑（`tests/conftest.py` 在 17:57 被**别的卡**改动之后，重新在同一工作区复跑同一子集）
$ python -m pytest tests/unit/test_s3_01_handover.py tests/unit/test_s1_02_s3_01_fixpoint_guard.py ^
    (test_descriptors_*.py) (test_digestion_*.py) -q -p no:randomly
1154 passed in 58.13s                    # 0 failed —— 与 conftest 变更前一致
```

- `git diff HEAD -- tests/unit/test_s3_01_handover.py` **输出为空** ⇒ 那条红所守的断言**一字未动**；
  本卡**没有**排除名单、`skip`、`xfail`、白名单。
- 未跑全量 `tests/unit`（124 分钟）——按任务书只跑定向子集。
- 附带核对：`test_descriptors_backfill.py` 的 `test_rerun_idempotent` / `test_dry_run_identical_and_non_mutating`
  （对返回字典做全量 json 比较）在新增字段后仍全绿 ⇒ 新字段是确定性的，且不干扰既有契约。

---

## ⑤「重建 → 入轨 → 再重建」不动点的**原始输出**

### 5.1 端到端 CLI 探针（真实主轨 + 1 条新增技能 = **LEDGER-1 场景的等价复现**）

台账起点 = **生产 `data/descriptors.json` 的副本**（sha `325ac2d1…`），主轨 = 生产主轨 + 1 条
新增技能 `runbook1-probe-skill`（22 → 23 条）。全部落点在
`%TEMP%\runbook1\probeE\`，`AUDIT_DB_PATH`/`AUDIT_ROOTS_PATH`/`CP_EVENTS_DIR` 均指向 tmp。

```text
ledger sha(before)      = 325AC2D14A692007C92C3F99E3B4A8FA75641E318AD181EEB727DAA3022FDEDE

--- run0：--no-ingest-stages（关掉自动收口 = 本卡之前的行为）---
写入计数: {'register': 1, 'provenance': 1, 'data_class': 1, 'risk_level': 1, ..., 'no_op': 30}
新 wire 资产: 1 条（cp.skill.runbook1-probe-skill）
[!] 仍需 S3-01 收口: 1 条未入轨 (cp.skill.runbook1-probe-skill)
    处置: python scripts/run_s3_01_ingest.py --execute  或 重跑本脚本（默认已自动收口）
run0 关收口后 sha       = 9CAD3712527EC2C8E55E47E17B6ED9A8927F41C1A1DA8746583398883FB2A2AF

--- run1：默认（重建 + 自动收口）---
全量重校验: 33 条, valid=33, errors=0, warnings=4        # 收口后（关收口的那次是 warnings=5）
写入计数: {'register': 0, ..., 'no_op': 31}
新 wire 资产: 0 条
S3-01 首次入轨收口: 本次调用内已自动执行，入轨 1 条，残留未入轨 0 条
S3-01 收口: 台账无未入轨资产（不动点）
run1 重建+入轨后 sha    = FB96E5F53D3DDDE1C44713E23EC7D398AB7305AA09AA9D8A18121A4A52F3711A

--- run2 / run3：再重建（同一份主轨、同一份台账）---
新 wire 资产: 0 条
S3-01 首次入轨收口: 本次调用内已自动执行，入轨 0 条，残留未入轨 0 条
S3-01 收口: 台账无未入轨资产（不动点）
run2 再重建后 sha       = FB96E5F53D3DDDE1C44713E23EC7D398AB7305AA09AA9D8A18121A4A52F3711A
run3 再重建后 sha       = FB96E5F53D3DDDE1C44713E23EC7D398AB7305AA09AA9D8A18121A4A52F3711A
```

⇒ **不动点成立**：收口后再重建两次，台账 sha256 **逐字节恒定**；且每一步之后 `stage=None` 数 = 0。
⇒ **LEDGER-1 的复发路径已被堵死**：主轨新增 1 条技能 + 默认重跑 ⇒ wire 与收口**在同一次调用完成**，
   不需要任何人记得跑 runbook（关掉自动收口时，那条命令会被**打印出来**）。

### 5.2 探针 A：当前真实台账已在不动点（自动收口是逐字节 no-op）

```text
副本 sha(before) = 325AC2D14A692007C92C3F99E3B4A8FA75641E318AD181EEB727DAA3022FDEDE
S3-01 首次入轨收口: 本次调用内已自动执行，入轨 0 条，残留未入轨 0 条
S3-01 收口: 台账无未入轨资产（不动点）
副本 sha(after)  = 325AC2D14A692007C92C3F99E3B4A8FA75641E318AD181EEB727DAA3022FDEDE
```

### 5.3 护栏测试侧的同一不动点（tmp 台账，pytest 内）

第 1 例断言链：`r1` 收口后 `fixed = sha256(ledger)` → `r2`（再重建）`_sha256(ledger) == fixed` → `r3` 仍 `== fixed`，
且每一步 `_stage_empty(ledger) == []`（5.1 的 CLI 形态在单测里被固化为可回归的断言）。

---

## ⑥ 未验证项与残留风险

**未验证**

1. **未跑全量 `tests/unit`**（22,633 条 / 124 分钟）。本卡只跑了 22 个文件（1154 passed / 0 failed）。
   本卡改动落在 `agent/descriptors/backfill.py`（被 36+ 个模块 import），全量无新红建议由发起方复跑确认。
2. **CLI 之外的调用方未逐一实测**：`full_backfill()` 走的仍是 `ingest_stages=False`（缺省），
   其行为按代码路径推断为逐字不变（未单独跑它的集成路径）。
3. **`--no-ingest-stages` 的反向兼容**只验证了打印与字段（探针 run0），未验证"关掉后由别的卡继续按老流程跑"的长链。
4. **并发**：本卡未验证"两个进程同时重建同一台账"下的行为（`DescriptorRegistry` 自带锁与原子写，
   但本卡未做压力验证）。
5. 探针均用 `--resolutions <tmp>`（裁定台账指向 tmp）；**未**用生产 `data/descriptors/resolutions.jsonl` 走一遍，
   故"生产裁定台账参与下的收口行为"未实测（代码路径相同，仅路径参数不同）。

**残留风险**

1. **审计链已追加**：自动收口会往 `data/audit/audit_chain.db` 追加 `descriptor.stage`（best-effort，与官方入口一致）。
   本卡**没有**在真实台账上跑过自动收口（探针全部落在副本上），故**本次未给生产链 +1**（见 ⑧ 的 mtime 证据）。
2. **CLI 默认行为变更（正向）**：`run_s1_02_backfill.py` 实跑现在会顺带写 stage。
   对"只想 wire、stage 由别的卡管"的用法，需显式加 `--no-ingest-stages`。已在 runbook 与 `--help` 写明。
3. **文档口径**：新 runbook 与既有归档结案报告之间没有交叉链接（避免篡改归档）；发现入口靠
   `scripts/run_s1_02_backfill.py` 的 docstring 与 `--help`。若后续卡要"统一 runbook 索引"，本卡未做。
4. **`tests/conftest.py` 正在被别的卡修改**（实测 17:57:00 被写）。本卡测试自带 fixture，不依赖它；
   但若该卡的会话级守卫与我的显式 bind 冲突（两者都 bind `facade.audit`），
   以最后一个 fixture 生效为准 —— 本卡 fixture 在 teardown 会还原 `previous`，无泄漏。

---

## ⑦ 回滚

本卡**只改受 git 跟踪的源码与文档**，且**未 commit/add**，故回滚 = 反向编辑（不要 `git checkout` 整文件）：

```powershell
# 方案 1（精确、推荐）：用反向替换脚本把本卡改动摘掉（保留 M8 等其它在途卡的改动）
python C:\Users\Administrator\AppData\Local\Temp\runbook1\revert_probe.py drop
#   预期：backfill.py sha 9de403ef… → facc9a03…（= HEAD + M8，18 insertions / 3 deletions）

# 方案 2：手工删掉这两处
#   agent/descriptors/backfill.py：
#     - 形参 ingest_stages、字段 newly_wired、s3_01_followup 调用、s3_01_followup() 函数块
#     - _apply_item 的 wired 形参与两处 wired.append
#   scripts/run_s1_02_backfill.py：--no-ingest-stages、ingest_stages= 传参、收口打印块
#   tests/unit/test_s1_02_s3_01_fixpoint_guard.py：整文件删除
#   docs/audit_skill_governance/LEDGER_REBUILD_RUNBOOK.md + RUNBOOK1.md：删除
```

**运行时**：本卡**没有**改动 `data/descriptors.json` / `data/audit/daily_roots.jsonl` /
`data/audit/audit_chain.db` / `data/events/events.jsonl` ⇒ **无需运行时回滚**。
若有人手工跑过 CLI 的自动收口而想退回：按 runbook §回滚 用
`data/digestion/descriptors_pre_ingest_<ts>.json` 覆盖（审计链 append-only，不抹痕迹）。

---

## ⑧ 残留物自证

### 8.1 两个运行时文件的 sha256 对照（**未变**）

| 文件 | 本卡开始前 | 本卡结束后 | 判定 |
|---|---|---|---|
| `data/descriptors.json` | `325AC2D14A692007C92C3F99E3B4A8FA75641E318AD181EEB727DAA3022FDEDE` | `325AC2D14A692007C92C3F99E3B4A8FA75641E318AD181EEB727DAA3022FDEDE` | **未变 ✓** |
| `data/audit/daily_roots.jsonl` | `04991152919985FC21EC3BECD877A9D0F04B82CC3FE0FC70EE40389C82DC2832` | `04991152919985FC21EC3BECD877A9D0F04B82CC3FE0FC70EE40389C82DC2832` | **未变 ✓** |

（两者与 LEDGER-1 §⑥ 回滚锚点记录的值一致；`data/audit/audit_chain.db` 的 mtime 停在 **16:46**（早于本卡），
即本卡的 pytest 运行**没有**给生产链追加任何记录。）

### 8.2 本卡时间窗内仓库内的文件变更（`LastWriteTime > 17:40`，排除缓存目录）

```text
rel                                           Length LastWriteTime
agent\descriptors\backfill.py                  77066 2026/9/26 17:53:57   ← 本卡（源码）
scripts\run_s1_02_backfill.py                   9990 2026/9/26 17:50:40   ← 本卡（源码）
tests\unit\test_s1_02_s3_01_fixpoint_guard.py  11536 2026/9/26 17:53:03   ← 本卡（测试）
tests\conftest.py                              75313 2026/9/26 17:57:00   ← **不是本卡**（别的卡在途改，见 ⑥-4）
agent\data\network_config.json / tool_trace.db* / data\blackbox / data\lifetrace / data\messages.jsonl …
                                                     ← **不是本卡**（工作区里并发运行的服务/别卡在写）
```

⇒ 本卡在仓库内**只写了 3 个文件**（1 个测试 + 2 个源码）+ 2 份新文档；未触碰任何运行时台账/事件/审计文件。

### 8.3 仓库外探针（可整目录删除，不影响仓库）

`C:\Users\Administrator\AppData\Local\Temp\runbook1\`：
`revert_probe.py`（反向替换/还原）、`count_my_diff.py`、`mk_probe_main.py`、`probe_cli.ps1`、
`probe_cli2.ps1`、`probe_cli3.ps1`、`runbook1_only.diff`、`nonempty_red.txt`、
`backfill_HEAD.py`、`backfill_without_rb1.py`、`backfill_runbook1_backup.py`、
`probeA/…`、`probeB/…`、`probeC/…`、`probeD/…`、`probeE/…`（各含台账副本、报告目录、审计链副本）。

### 8.4 本卡 5 个文件的内容 sha256（供回滚核对）

| 文件 | sha256 |
|---|---|
| `agent/descriptors/backfill.py` | `9DE403EFBBBB3102988A8F63F5DC960EFCD5353F4A85FD44F74C1604097783B7` |
| `scripts/run_s1_02_backfill.py` | `774FBBD34E89034A8380DF76D0FAB9FF06CE3BF3F96816D7467044953E295B49` |
| `tests/unit/test_s1_02_s3_01_fixpoint_guard.py` | `C52A23723FAED456AF8887A0A476B6ED3C487756495FD20A0B4BB5DD03841FDD` |
| `docs/audit_skill_governance/LEDGER_REBUILD_RUNBOOK.md` | `8F271C72A7E79C4B498AE84AA3E3D87A4096032A6886ED8A5A6F5B64E6569A69` |
| `docs/audit_skill_governance/RUNBOOK1.md` | 本报告自身（自指，不列） |

（`backfill.py` 的"摘掉本卡改动"版本 sha = `FACC9A031474A5E2464B2E8718DA73D0340E66A33080284FC17963993ED32DC4`，
其与 `HEAD` 的差异恰为 M8 的 18 insertions / 3 deletions —— 见 ②。）

### 8.5 禁止项自检

- 未 `git commit` / `git add` / `git checkout` / `git stash`（全部 `git diff` / `git show` 均为只读）。
- 未触碰：`agent/skills_mgmt/**`、`tests/unit/conftest.py`、`agent/tool_router*.py`、`agent/audit/**`、
  `plugins/**`、`yunshu-ui/**`、`config.yaml`、`data/audit/daily_roots.jsonl`、`data/learned_workflows.json`。
- **未在 `data/descriptors.json` 上做任何实验**（所有探针/测试都在 tmp 副本或 `tmp_path` 上），
  也没有把它弄回未入轨状态。
- 未 `taskkill` 任何 python 进程；未起常驻服务；未使用 `venv/`；**未出网**（零网络访问）。
- 本卡只跑了**只读** git 命令：`git status` / `git diff` / `git diff --no-index` / `git show`。

**一处观察（不是本卡所为，知情登记）**：`git diff --cached --name-only` 显示索引里有两条**已被 stage**
的文件 —— `data/learned_workflows.json`（-517）与 `data/skills_repo/.migration/descriptions.baseline.json`（+360，新文件），
`.git\index` 的 mtime = 17:55:34（本卡窗口内，但**不是本卡**：本卡从未执行 `git add`）。
工作区里同时有别的卡/服务在跑（同期还有 `tests/conftest.py` 17:57:00、`data/lifetrace/**` 17:56 等**非本卡**写入）。
本卡**刻意不动索引**（不 `add`、不 `reset`、不 `restore`），以免破坏在途卡的分阶段提交状态。
