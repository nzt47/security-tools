# TASK-S7-02 验收报告 · 自动诊断与补丁 PR（自修复 L1：能修，但不自动落地）

> 任务书：[`TASK-S7-02_自动诊断与补丁PR.md`](TASK-S7-02_自动诊断与补丁PR.md)｜批次总表：[`PARALLEL_S7批次总表.md`](PARALLEL_S7批次总表.md)
> 基线：`master` / `531515e0`（开工时 HEAD `8f22974f`，仅 doc 提交之差）｜worktree：`s702`｜日期：2026-09-12
> 依赖：S4-04（真子代理）/ S5-02（L0 锚）/ S3-02（回放沙箱）/ S2-01·S2-02（Trace/审计）——均已结案
> 端到端证据：[`evidence/TASK-S7-02_端到端样例_20260912.json`](evidence/TASK-S7-02_端到端样例_20260912.json) + [`evidence/TASK-S7-02_端到端样例_20260912.md`](evidence/TASK-S7-02_端到端样例_20260912.md)

---

## 〇、一句话结论

**L1 成立**：本体注入一个真实小 bug → 体检发现（含失败指纹）→ 定位（源码切片 + 只读 git 证据）→ 八要素派工 →
**隔离验证三关（真实 pytest + 真实 L0 锚）全过** → 产出**本地**分支 + 补丁 + PR 描述 → **人工合入后 L0 锚 20/20 通过**。
四条宪法式边界逐条有**代码级**落点；五步全程留 Trace + 链式审计且 `verify_chain` 通过；"代码路径无 push/merge"以**三种独立手段**自证。

| 关键量 | 值 | 来源 |
|---|---|---|
| 新增生产代码 | `agent/repair/` **14 模块 5225 行** + `scripts/` 3 脚本 692 行 | `Get-ChildItem` 行计 |
| 新增单测 | **8 套件 285 例全绿** | `pytest tests/unit/test_repair_*.py -q` |
| `agent/repair` 覆盖率 | **89%**（单模块 80%–100%） | `--cov=agent.repair` |
| 邻接回归 | **544 passed / 0 failed**（subagent + eval + digestion） | 见 §七 |
| 本地门禁 | kwarg 扫描两条 **0 处**；mypy 新增模块 **0 error**；lint-imports **2 kept/0 broken** | 见 §七 |
| 端到端 | 注入 bug → PR → 人工合入 → **L0 锚 20/20** | §六 |

---

## 一、交付物清单

| # | 交付物 | 位置 | 说明 |
|---|---|---|---|
| 1 | 自修复包 | `agent/repair/`（`policy`/`models`/`gitio`/`trace`/`budget`/`diagnose`/`locate`/`guardrails`/`delegate`/`patchapply`/`verify`/`propose`/`pipeline`/`__init__`） | 五步：diagnose→locate→delegate→verify→propose |
| 2 | 手动入口 | `scripts/self_repair.py`（182 行） | 只做**手动触发** + 体检报告；默认不接自动触发 |
| 3 | 端到端演示 | `scripts/demo_s7_02_repair.py`（374 行）+ `scripts/repair_demo_support.py`（136 行） | `inject`（注入+跑流水线）与 `apply`（人工合入+复验）两阶段 |
| 4 | 隔离验证三关 | `agent/repair/verify.py` + `patchapply.py` | 临时副本 + 目标用例 fail→pass + L0 锚 + 邻接回归；**不过即丢弃** |
| 5 | 范围护栏 | `agent/repair/guardrails.py` + `policy.py` | 只读区黑名单 / 文件数 ≤3 / 单文件 ≤120 行 / 改测试显式标注 |
| 6 | 预算与轮次上限 | `agent/repair/budget.py` | 超限抛异常**硬停**并留证 |
| 7 | 本地补丁 PR 产物 | `data/repair/`（**已入 .gitignore**） | 分支 + 补丁 + PR 描述 + 体检报告 + 运行报告 |
| 8 | 单测 | `tests/unit/test_repair_{diagnose,guards,delegate,verify,no_push,pipeline,locate,trace_git}.py` + `tests/unit/repair_fixtures.py` | 285 例 |
| 9 | 本报告 + 结案报告 | 本目录 | — |

新增文件不触碰任何既有公开接口；`tests/unit/conftest.py` 仅**追加**一个 `run_logger` 夹具（隔离留痕器）。

---

## 二、任务书 §四 验收清单（逐条）

| # | 验收项 | 结论 | 证据（可复现命令 / 断言） |
|---|---|---|---|
| 1 | 人为注入的真实小 bug 能被体检器发现，并产出 `DiagnosisReport`（含失败指纹与相关改动） | ✅ | `pytest tests/unit/test_repair_diagnose.py -q`（25 例：`test_finds_injected_failure` / `test_fingerprint_*` / `test_git_root_returns_commits_and_interest`）；端到端见 §六（3 条失败 + 3 个指纹 + 8 条近期改动） |
| 2 | 八要素不全时**拒绝派工**（用例） | ✅ | `test_repair_delegate.py::TestEightElementGate`（8 个单点缺失参数化 + 端到端拒绝：**执行器零调用**；拒绝原因点名 `goal`） |
| 3 | 子代理无写权限/审批权（工具裁剪断言） | ✅ | `TestToolTrim`（8 例）：白名单只读；真实 `SubAgentToolset` 对 `memory.write`/`approval.approve`/`core.rewrite` **申请也不可见**；`write_file` 靠"授权子集 = 只读白名单"拦下；裁剪集出现写类工具 → 本次产出作废（`E_REPAIR_TOOLSET_UNSAFE`） |
| 4 | 只读区改动被拒；文件数/行数超限被拒；改测试断言会在 PR 描述中显式标注 | ✅ | `test_repair_guards.py`（30 例）：`core/auth/`·`core/audit/`·`schema/`·`agent/security/`·`agent/audit/chain.py`·`eval/l0_anchor/` 逐条参数化拒绝；4 文件/201 行拒绝、3 文件/120 行放行；测试改动**不拒但机械识别**，`pr_description` 强制插入警示块（`test_test_change_is_explicitly_flagged`） |
| 5 | **补丁未通过三关则不产出**（含"故意失败即丢弃"用例） | ✅ | `test_repair_verify.py`（24 例）逐关单点失败 + 打补丁前即通过 → 基线不成立也不放行 + 应用失败放行=否；`test_repair_pipeline.py::test_verification_failure_discards_everything`（status=discarded、`proposal is None`）；`propose()` 在未验证时抛 `ProposalNotVerified`（3 例） |
| 6 | 产出物仅本地（**代码级断言无 push/无 merge 调用**） | ✅ | `test_repair_no_push.py`（22 例）三种独立手段，见 §四 |
| 7 | 全流程 Trace + 链式审计可查（五步都有记录），`verify_chain` 通过 | ✅ | `test_repair_pipeline.py::test_audit_trail_has_five_steps_with_actions`（五步各一条 + 每条恒有 `trace_id` + `action=repair.*` + `actor=auto`）；`audit_completeness()` 机械化判定；端到端 `chain_ok=True`、审计 seq 324→329 连续 |
| 8 | 单次修复预算与轮次上限生效（超限即停并留证） | ✅ | `test_repair_delegate.py::TestBudget`（6 例）+ `TestPipelineBudget`（3 例）：余额不足**派工前**即停、消耗超额停在委派步、`max_rounds=1` 时执行器只被调用 1 次；`budget_notes` 留证 |
| 9 | 端到端样例可复现（注入 bug → 产出 PR → 合入后 L0 通过） | ✅ | §六（两阶段命令 + 全量证据） |
| 10 | 既有 `subagent`/`eval`/`digestion` 套件零回归；新增单测全绿、覆盖率 ≥80% | ✅ | §七：544 passed/0 failed；285 例全绿；`agent/repair` **89%** |

---

## 三、四条宪法式边界的代码级落点

| 边界 | 落点（代码） | 反向证据 |
|---|---|---|
| ① 绝不自动 push / 合并 | `gitio.READONLY_SUBCOMMANDS` 白名单 + `assert_readonly_subcommand()`（拒绝含 `push`/`merge`/`create_pull_request` 等）；唯一写操作 `gitio.create_local_branch()` 只建**本地引用**、不 checkout；`RepairProposal.pushed/merged` **恒 False** | `test_repair_no_push.py` 22 例（见 §四）；端到端沙箱 `git remote` 输出为空 |
| ② 绝不触碰只读区 | `policy.READONLY_ZONE_PREFIXES`（`core/auth/`、`core/audit/`、`schema/`、`agent/security/`、`eval/l0_anchor/`）+ `READONLY_ZONE_FILES`（`agent/audit/chain.py`）+ `READONLY_NAME_PATTERNS`（审计/签名类文件名 fail-closed）；`guardrails.guard_patch()` 命中即整包丢弃 | `test_repair_guards.py::TestReadonlyZones`（参数化 8 命中 / 6 不命中）；端到端 `test_readonly_patch_is_rejected_before_verification`（护栏拒绝的补丁**不进隔离验证**：`run_anchor` 调用计数为 0） |
| ③ 验证不过就不产出 | `propose.propose()` 在 `verification is None or not ok` 时抛 `ProposalNotVerified`（**结构性拒绝**，pipeline 无法绕过）；`VerificationReport.ok` 只在三关全过时为真；`CheckResult.effective_pass` 对"未运行/跳过" fail-closed | `test_repair_pipeline.py::TestProposalGate`（3 例：未过/缺验证 → 异常且**零产物落盘**） |
| ④ 全程可审计（不许不可见动作） | `trace.RepairRunLogger.run_step()` 在**成功/失败/异常**三路径都写 Trace + 链式审计；`record_step()` 缺省自生成 `trace_id`；`pipeline.audit_completeness()` 按终态给出"应留痕步骤集" | `test_repair_trace_git.py::TestRepairRunLogger`（11 例：异常路径也留痕且**原样重抛**、`missing_steps()` 可判定、`verify_chain()==True`）+ 端到端五步 seq 连续 |

---

## 四、"无 push / 无 merge" 的自证（硬验收项）

`tests/unit/test_repair_no_push.py` 用**三种彼此独立**的手段覆盖三种不同失效模式：

| 手段 | 覆盖的失效模式 | 断言内容 |
|---|---|---|
| ① **源码扫描**（AST + tokenize 剔除注释与 docstring） | 「有人后来加了一行 push」 | `agent/repair/` 全包 + `scripts/self_repair.py` 的**代码行**中不得出现 `"push"`/`"merge"`/`"rebase"`/`"reset"`/`"checkout"`/`"commit"`/`"remote"` 子命令字面量，以及 `gh pr create` / `create_pull_request` / `requests.post` / `urlopen` |
| ② **运行时探针**（monkeypatch `gitio.subprocess.run`） | 「通过未扫描到的包装间接调用」 | 跑本包全部真实 git 路径（`repo_head`/`head_changed_files`/`recent_commits`/`is_git_repo`/`branch_exists`/`create_local_branch`），断言**每条**命令的子命令都在只读白名单内；被拒命令**不会真的执行**（探针零记录） |
| ③ **数据结构自证** | 「把 push 的成果记成已 push 来误导人」 | `RepairProposal`/`BranchResult` 的 `pushed`/`merged` **恒 False**；两个数据类里**根本不存在** `remote`/`pr_url`/`merge_command` 一类字段 |

**元测试**（防止"永远通过"的摆设）：扫描器对合成违规必须报警；对注释/docstring 必须不误报；声明式禁用清单**仅当显式登记**时豁免（`FORBIDDEN_SUBCOMMANDS` 用 AST 精确定位，不靠行号硬编码）。

端到端侧证：演示沙箱内 `git remote` 输出为空字符串（从未添加远端）；产物分支存在但工作区分支未切换（`git rev-parse --abbrev-ref HEAD` 仍为 `main`）。

---

## 五、五步留痕（端到端实测）

| 步 | 审计 action | 状态 | seq | trace_id（前 10 位） | 说明 |
|---|---|---|---|---|---|
| 1 体检 | `repair.diagnose` | **error**（发现失败） | 324 | `7fb6e69f71` | 3 条失败 + 3 个指纹；L0 锚全过 |
| 2 定位 | `repair.locate` | ok | 326 | `823a93b17f` | 工单 `tkt-*`；切片 + 实现文件 + 近期改动 + 证据缺口 |
| 3 派工 | `repair.delegate` | ok | 327 | `158d66e238` | 八要素齐备；工具裁剪只读；护栏通过 |
| 4 验证 | `repair.verify` | ok | 328 | `b18f3e17c5` | 三关全过；`copy_cleaned=True` |
| 5 产出 | `repair.propose` | ok | 329 | `c0aca3dad6` | 本地分支创建成功；`pushed=False`/`merged=False` |

- 审计链校验：`chain_ok = True`（`AuditFacade.verify()` → `chain.verify_chain()`）。
- 完整性判定：`audit_completeness(report).ok = True`（五步齐全）。
- 「无失败」时**只**留 `diagnose` 一条（不伪造后四步）——见 `test_no_failure_is_legal_and_stops_early`。
- 被丢弃时留 `diagnose/locate/delegate/verify`，且 `verify` 的 `status=error` 带"丢弃"原因（"丢弃"本身也是**可见动作**）。

---

## 六、端到端样例（完整证据链）

### 6.1 复现命令

```powershell
# 沙箱在系统临时目录，交付工作区**一个字节都没被改**
python scripts/demo_s7_02_repair.py inject --repo-root . `
    --workspace "$env:TEMP\cp-s702-demo\ws" --artifacts "$env:TEMP\cp-s702-demo\art"
python scripts/demo_s7_02_repair.py apply  --workspace "$env:TEMP\cp-s702-demo\ws" `
    --artifacts "$env:TEMP\cp-s702-demo\art"
```

### 6.2 注入的 bug（真实、可机械判定）

沙箱内新建 `agent/demo_s7_02.py` + `tests/unit/test_demo_s7_02.py`（仓库本体**不含**这两个文件），
把 `increment(n)` 实现写成 `return n - 1`（应为 `n + 1`）——off-by-one，3 条断言同时失败。

### 6.3 阶段 1：体检 → 派工 → 验证 → 产出

| 环节 | 实测结果 |
|---|---|
| 体检失败项 | `tests.unit.test_demo_s7_02::test_increment_positive` @ `tests/unit/test_demo_s7_02.py:7` 指纹 `b4ba276262b11f23`；`zero`@:11 `8abacdf55e079975`；`negative`@:15 `bd30e7407580657d` |
| 体检命令 | `python -m pytest -q --tb=short --no-header -p no:randomly --continue-on-collection-errors --junitxml=... tests/unit/test_demo_s7_02.py` |
| 体检时 L0 锚 | **20/20 全过**（说明"标尺没坏，是代码坏了"） |
| 近期改动 | 8 条（`git log` 提交 + HEAD 改动文件 + 关注文件 mtime） |
| 定位工单 | 切片含失败文件真实行号；实现文件 `agent/demo_s7_02.py` 由 import 反推 |
| 派工 | 八要素齐备；`tools = [read_file, list_dir, grep, read_only_git]`；`authorized_subset` 同集合 |
| 护栏 | 通过：1 文件 / 2 行（上限 3 文件 / 120 行）；未命中只读区；未改测试 |
| 补丁 sha256 | `c9ab07d044116fd5c8226f6924af6b1a`（PR 描述引用该哈希，防事后替换） |
| **三关** | 目标用例 **fail→pass**（`target_failed_before=True`，after `exit=0`，1 passed）；L0 锚 **20/20**；邻接回归 `tests/unit/test_demo_s7_02.py` **3 passed** |
| 产物 | 分支 `repair/20260912-tests-unit-test-demo-s7-02test-increment-positiv`（**本地**，创建成功）；补丁 295 字符；PR 描述 4612 字符；体检报告 md |
| PR 描述 | 含问题摘要 / 根因分析（引 Trace 与近期改动）/ 补丁说明 / 三关证据表 / 风险与回滚 / **6 条人工复核重点** / 复现命令；`undo_hint` 给出 `git branch -D` + `git revert` + `git apply -R` 三条可执行口径 |
| 未 push / 未合并 | `proposal.pushed=False`、`proposal.merged=False`；沙箱 `git remote` 为空 |

### 6.4 阶段 2：人工合入 → 复验

| 环节 | 实测结果 |
|---|---|
| 人工合入 | `apply_unified_diff` 打补丁到沙箱（`applied=['agent/demo_s7_02.py']`），随后提交（模拟人工 review 后合入） |
| 目标用例 | `exit=0`，**3 passed** |
| **L0 锚** | **20/20 全过**（`integrity_ok=True`，reference 解算器） |
| 复跑体检 | **未发现失败**（`ok=True`） |
| 总判定 | `merge.demo_ok = true` → 脚本输出「端到端演示成立」 |

### 6.5 真实/桩的边界（如实声明）

| 类别 | 内容 |
|---|---|
| **真实** | 体检（真跑 pytest + 真解析 junitxml）、定位（真读 git 历史 + 真读 L0 锚）、护栏、**隔离验证三关**（真复制仓库 → 真打补丁 → 真跑 pytest → 真跑 L0 锚 → 真清理副本）、产出（真建本地分支、真写补丁与 PR 描述）、审计（真写 Trace + 真写链式审计并真校验 `verify_chain`） |
| **桩** | **子代理的产出**：本环境无外部 agent CLI 凭据（任务书 §八 已知坑 1「不要真调外部 agent CLI」），故以固定 diff 的**通道桩**代替子代理；它只模拟"返回一份补丁文本"，其后一切机制不减免。该边界已写入 `evidence.json` 的 `stub_boundary` 字段与脚本 docstring |
| 未声称 | 真实模型产出的补丁质量；L2（白名单内自动合入）；L3（熔炉核心自改写）——均**不在本任务范围** |

---

## 七、本地门禁与回归

| 门禁 | 命令 | 结果 |
|---|---|---|
| 新增单测 | `pytest tests/unit/test_repair_*.py -q` | **285 passed / 0 failed** |
| 覆盖率 | `pytest ... --cov=agent.repair` | **89%**（`__init__`/`models`/`budget` 100%；其余 80%–95%） |
| 邻接回归（subagent） | `pytest tests/unit/test_subagent{,_delegation,_executor,_toolset}.py -q` | 全绿 |
| 邻接回归（eval + digestion） | `pytest tests/unit/test_eval_{anchor,runner,cases}.py tests/unit/test_digestion_{gate,sandbox}.py -q` | 全绿（**合计 544 passed / 0 failed**） |
| kwarg 扫描（两条） | `python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH` / `--path tests` | **各 0 处**（exit 0） |
| mypy（新增模块） | `python -m mypy agent/repair/` | `agent/repair` **0 error**（其余为既有阻塞模块，与 master 逐条同现） |
| mypy（新脚本） | `python -m mypy scripts/self_repair.py scripts/demo_s7_02_repair.py scripts/repair_demo_support.py --follow-imports=silent` | **0 error** |
| importlinter | `lint-imports` | **2 kept / 0 broken** |
| 产物漂移 | `git status --short` | 仅本任务新增/修改文件；`data/repair/` 已在 `.gitignore` |

**实现期修复的真实缺陷（3 处，均由测试/演示暴露）**

1. **junit `classname` 不是 pytest 节点 id** —— 直接把 `tests.unit.test_x::test_f` 喂给 pytest 会得 `no tests ran`，
   目标用例闸门"看起来失败"而实际是被选不中。修复：`verify.node_target()` 做形态转换（`classname` 点分名 → 文件路径 :: 函数名）。
2. **旧 `__pycache__` 串味** —— 沙箱复用导致新写入的模块导入到上一版字节码，表现为 pytest `collection failure`。
   修复：`scripts/repair_demo_support.purge_pycache()` 在复制后清理。
3. **Windows 上被占用目录的 `rmtree` 静默失败** —— 随后的 `shutil.move` 会把副本嵌套进已存在目录（`<target>/<repo>/...`），
   后续路径全错。修复：沙箱构建对已存在目标根**显式报错**并要求 `overwrite=True`，且移动后校验结构。

另有两处**口径**修正：失败指纹的归一化不能抹掉普通数字（否则 `assert x == 2` 与 `== 3` 撞指纹）；
Trace 查询前必须 `flush()`（`UnifiedTraceStore` 是"入队即返回、后台批量写"，不 flush 会看不到本次运行自己的记录）。

---

## 八、任务书 §五「明确不做」的确认

| 不做项 | 确认 |
|---|---|
| 自动触发（定时/告警驱动） | ✅ 未做。入口只有**手动** CLI；`run_repair` 无任何调度/订阅；巡检无定时器 |
| 自动 push / 自动合并 / 自动开远端 PR | ✅ 未做（§四 三手段自证） |
| 核心自改写（v7.2 §5.2 熔炉） | ✅ 未做；`core.*`/`forge.*` 工具在裁剪层被拒 |
| 修改只读区与审计/签名相关代码 | ✅ 未做（§三 边界 ②）；本任务对 `agent/audit/` 只**调用** `facade`/`chain.verify_chain()`，未改其源码 |

---

## 九、遗留与移交（带归属与阻塞性）

| # | 遗留 | 归属 | 阻塞性 | 说明 |
|---|---|---|---|---|
| L1 | 真实子代理产出补丁的质量未评测 | S7 后续 / Owner | 非阻塞 | 本环境无外部 agent CLI 凭据；`DelegationExecutor` 真实通道已就绪，接上凭据即可跑真实路径。**未声称**已评测 |
| L2 | 默认体检范围是"冒烟子集"（`DEFAULT_TEST_TARGET` 2 个文件） | 使用方 | 非阻塞 | 全量 `tests/unit` 547 文件对"每次体检"过重；用 `--test-target` 可指定更宽范围，口径在报告 `test_command` 中如实可见 |
| L3 | 邻接回归映射基于"测试文件 import 扫描" | S7 后续 | 非阻塞 | 覆盖本项目同构命名；更强的映射（覆盖率数据）可替换 `locate.infer_impl_files()` 单点 |
| L4 | 轮次重试时 `locate`/`delegate` 会重复留痕 | 本任务已披露 | 非阻塞 | 属"如实记录每一次尝试"，非缺陷；`audit_completeness` 按"步骤集合"判定而非计数 |
| L5 | L2 白名单内自动合入 / L3 熔炉 | Owner 决策 | 阻塞（范围外） | 任务书 §五 明确不做；本任务交付的是"能修但不自己落地" |

---

## 十、结论

- 任务书 §四 十条验收项 **10/10 通过**；四条宪法式边界**逐条有代码级落点与反向证据**；
  §五「明确不做」四项**全部未做**并已确认。
- 端到端链路（注入 bug → 体检 → 定位 → 八要素派工 → 隔离验证三关 → 本地补丁 PR → 人工合入 → L0 通过）**成立且可重复执行**。
- **未声称**：真实模型补丁质量、L2/L3 层级能力、全量测试套件的体检时延。
