# TASK-S7-05 验收报告 — 真实数据打通（真实内化演示 + 治理面板出真数）

> 任务书：[`TASK-S7-05_真实数据打通.md`](TASK-S7-05_真实数据打通.md)｜批次总表：[`PARALLEL_S7批次总表.md`](PARALLEL_S7批次总表.md)
> 演示方案：[`../真实内化演示方案.md`](../真实内化演示方案.md)｜端到端记录：[`真实内化演示记录.md`](真实内化演示记录.md)
> worktree：`s705`（代码）｜基线：`master` / `531515e0`（开工点 `8f22974f`）｜执行：2026-09-12
> 运行时根：`C:\Users\Administrator\agent`（**真实数据落点**，即治理面板读取的位置）

---

## 一、交付物清单（对照任务书 §三）

| # | 任务书要求 | 交付物 | 状态 |
|---|---|---|---|
| 1 | `docs/zh/真实内化演示方案.md`（含门槛裁定与开关快照） | [`docs/zh/真实内化演示方案.md`](../真实内化演示方案.md) | ✅ |
| 2 | 真实轨迹采集记录（同类条数按能力统计 + 未达标清单） | [`真实内化演示记录.md`](真实内化演示记录.md) §二 + `data/digestion/s705_demo/s705_采集记录.json`（315 条明细 / 含 `registry_coverage.below_threshold` 29 项） | ✅ |
| 3 | ≥1 个能力的端到端消化记录（清洗→挖掘→判定集→验收门→灰度→内化→PR） | [`真实内化演示记录.md`](真实内化演示记录.md) §三（六阶段逐步留证） | ✅ |
| 4 | 面板三列显示真实数字（截图或接口输出证据） | 本文 §4.2–4.4（两种读法逐字一致）+ `s705_面板读数.json` | ✅ |
| 5 | `TASK-S7-05_验收报告.md` + `真实内化演示记录.md` | 本文件 + [`真实内化演示记录.md`](真实内化演示记录.md) | ✅ |
| 6 | 代码与测试 | `agent/digestion/real_capture.py`、`agent/digestion/switch_snapshot.py`、`scripts/s705_real_digestion_chain.py`、`scripts/s705_panel_probe.py`、`tests/unit/test_s7_05_real_data.py`（33 例） | ✅ |

---

## 二、验收清单逐条（任务书 §四，每条附证据命令）

### ✅ 1. 演示门槛（≥5）与正式门槛（≥20）在方案、报告、UI 数据旁**均有明确标注**，不得混淆

- 方案：`docs/zh/真实内化演示方案.md` §一 给出两门槛对照表 + "两者在报告与 UI 数据旁的区分方式"四条硬性规定；
- 脚本 stdout：`scripts/s705_real_digestion_chain.py` 采集阶段**同时**打印两行，各自带 `达标=` 布尔：
  ```
  同类轨迹（正式门槛 ≥20）：max=284｜达标=True
  同类轨迹（演示门槛 ≥5）：达标=True
  ```
- 报告与记录：本文件与演示记录**首页**均写明"本轮实测 284 条 ≥ 正式门槛 20，故一律按正式门槛判定；
  演示门槛仅用于演示声明"；
- **本轮未依赖演示门槛放行任何一步**（`DigestionService(threshold=20)`、`GATE_REPLAY_MIN=20`、
  `DIGEST_COUNT_MIN=50`、`MONTHLY_SAMPLES_MIN=200` 全部取模块常量，代码中无任何下调）。

证据命令：`python scripts/s705_real_digestion_chain.py`（输出见演示记录 §一）；
`grep -n "DEMO_THRESHOLD\|FORMAL_THRESHOLD" scripts/s705_real_digestion_chain.py`。

### ✅ 2. 轨迹为**真实执行**（含真实指令/工具调用/结果状态），报告中给出判定依据；**未使用合成轨迹**

- 七条判定依据逐条可核：[`真实内化演示记录.md`](真实内化演示记录.md) §2.1；
- 真实任务 = 真实工具（`read_file` / `execute_shell` / `write_file`）+ 真实子进程 + 真实写盘 + 真实退出码；
- 315 条任务中 **31 条真实失败**（真实 pytest `exit=1`，被测模块带真实缺陷），
  任务级 Trace 如实落 `error`，**未补成功轨迹、未改写结果**；
- `agent/digestion/real_capture.py` 不生成任何虚构 Trace 行（模块文档守则 + 代码结构：每行都由
  `facade.record()` 在真实调用后写入，`output` 即真实返回值本体）。

证据：`data/digestion/s705_demo/s705_采集记录.json::results[*].test_exit_codes`
（`{"0": 284, "1": 31}`）与 `results[*].trace_ids`（每个任务 2–3 个真实 trace_id）。

### ✅ 3. 至少 1 个能力跑完六阶段链路，每阶段有可复现命令与输出

`cp.builtin.read_file` 全程跑通，六阶段命令与关键输出见 [`真实内化演示记录.md`](真实内化演示记录.md) §三：
① `pipeline()`（pattern `pat_3f2fcbc922e041b8`，284 条支撑，骨架 3 步）→
② 判定集 v22（64 组 = Seed 4 + Trace 60，带 `origin_trace_id`）→
③ 验收门 4/4 通过（executed 60，通行证 `pp_d8912c3d6460dcdb15d2`，`mirrored→shadow` 真实落库）→
④ 灰度 21 次（预算 `min(315×15%,50)=47`，通过率 1.0，真实墙钟 p99）→
⑤ 内化六条件齐备（`promote`，rank 1.0）→
⑥ 9 个 `stage.promote` PR（本地，`pushed=false/merged=false`）。

### ✅ 4. 灰度期 `ShadowReport` 含**真实墙钟 p99** 与 `judge_kind`；未使用 mock judge 冒充

```
p99_wall_candidate_ms = 0.121  p99_wall_upstream_ms = 0.155   ← perf_counter 实测（同批双跑）
p99_model_candidate_ms = 14.0  p99_model_upstream_ms = 14.0   ← 仅披露，不作阈值
judge_kind = deterministic_local(llm_unavailable)             ← 无 LLM 凭证 ⇒ 引擎如实回落
```

口径字段：`SHADOW_VERSION` 报告内 `clock = "wall_clock(perf_counter; per-arm real elapsed)"`。
**未**注入自定义 judge 冒充 `llm_judge`（`judge.is_llm=false` 逐字写入台账与面板卡片）。

### ✅ 5. 内化决策如实执行六条件；**未通过项按实记录**（不得调参刷过或在报告中隐去）

- 六条件实测：① 90 ≥ 50 ② 362 ≥ 200 ③ ROI 0.5611 > 0 ④ 1.0 ≥ 0.98 ⑤ 0.121 ≤ 0.155 ⑥ pass；
- **按实记录的未通过项**：21 次灰度中**周期 11 因真实墙钟噪声触发⑤一票否决**
  （候选 0.125ms > 上游 0.122ms，判决 `veto_blocked`，**未产出 PR、未调参放行**），
  22 个周期的 p99 比值分布（min 0.547 / 中位 0.774 / max 1.025）逐周期列出；
- 全部 22 个周期的判决序列（含 10 次 `low_traffic_manual`）在 `s705_链证据.json::cycles` 中逐条保留；
- 六条件门槛全部取 `internalize.py` 模块常量（50 / 200 / 0.98 / 1.0），**无一处下调或注入证据**；
  唯一的人为改动是 `trust.data_class` 的**分类回填**（`null → internal`），前后值与理由入证据与审计（见 §五）。

### ✅ 6. `stage.promote` PR 为**本地可审阅产物**，未自动 push / 未自动合并

```
pr_id = pr_f824c67abb1cc057     branch = digest/promote-cp.builtin.read_file-bb1cc057
files = APPLY.md, PR_DESCRIPTION.md, ROI_REPORT.md, decision.json, stage_promote.patch
pushed = false     merged = false
note = "本地可审阅产物（补丁 + PR 描述 + ROI + 合入说明）；未推送远端、未自动合并"
```

**本任务全程未执行任何 `git push` / `git merge` 于 stage 语义**（`create_promote_pr` 不含 subprocess 调用；
可在代码中核对）。目标态 `internalized` **未落库**（台账仍为 `shadow`），合入步骤写在 `APPLY.md`，属人工动作。

### ✅ 7. 面板三列显示真实数字（附证据）；**无演示填充数据**

见本文 §四（两种读法交叉验证）：验收 **42**、灰度 **42**、内化决策 **9**，`shadow_runs` **21**，
`stage_distribution {"shadow": 1, "borrowed": 31}`。数据全部由真实链路写入真实运行时目录；
脚本**未**向面板注入任何常量，也**未**在面板侧做任何特判。

### ✅ 8. 演示期开关快照已记录，**结束后已复位**（对比证据）

```
演示前 ↔ 演示期：added=["CP_DIGESTION_SHADOW_ENABLED"]，生效值 False→True
演示前 ↔ 演示后：identical=true（added/removed/changed/effective_changed 全空）
```

三份快照 + 单测断言（`TestSwitchSnapshot`、`test_demo_switch_enable_and_reset`）见演示记录 §五。

### ✅ 9. 报告中含"未完成事项与所需条件"（诚实边界）；未达标能力清单保留

- 未完成事项 8 条（LLM-judge 缺位 / p99 噪声敏感 / 灰度未真实接管 / 29 个能力零真实流量 /
  真实调用能力级 Trace 断点 / 面板两列落位口径 / PR 与抽检待人工 / 时间压缩），逐条给出**所需条件**：
  [`真实内化演示记录.md`](真实内化演示记录.md) §七；
- 未达标能力清单：`registry_coverage` 实测 **达标 3 / 未达 29**，29 项逐条列出（能力 id + stage + 真实同类轨迹条数 0）。

### ✅ 10. 相关套件（`digestion`/`ui_panels`/`eval`）零回归；新增/改动单测全绿、覆盖率 ≥80%

见本文 §六（质量证据）。

---

## 三、新增/改动代码

| 文件 | 类型 | 行数 | 职责 |
|---|---|---|---|
| `agent/digestion/real_capture.py` | 新增 | 553 | 受控真实工作区构造 + **真实工具调用 → 能力级统一 Trace** + 同类门槛核对 + 全台账覆盖清单 |
| `agent/digestion/switch_snapshot.py` | 新增 | 296 | 消化链开关的只读快照（原始值 + 引擎生效值）与前后对比（复位机器判据） |
| `agent/digestion/__init__.py` | 改动 | +26 | 导出新模块公共符号（**刻意不导出同名函数 `switch_snapshot`**，避免把包属性从子模块覆盖成函数） |
| `scripts/s705_real_digestion_chain.py` | 新增 | 643 | 端到端入口：真实采集（分批）+ 每批一次真实消化周期 + 面板取证 + 开关复位 + 归档 |
| `scripts/s705_panel_probe.py` | 新增 | 232 | 面板三列读数取证（显式目录口径 / 面板默认口径 + `--code-root` 交叉验证） |
| `tests/unit/test_s7_05_real_data.py` | 新增 | 539 | 33 例：采集真实性形状 / 失败路径 / 路径口径回归 / 门槛统计 / 开关复位 / 面板取证 |
| `docs/zh/真实内化演示方案.md` | 新增 | — | 门槛裁定 + 开关快照 + 真实性判定依据 + 复现入口 |
| `docs/zh/CloudPivot_v7.2重构计划/真实内化演示记录.md` | 新增 | — | 端到端命令与输出 |
| `docs/zh/CloudPivot_v7.2重构计划/TASK-S7-05_验收报告.md` | 新增 | — | 本文件 |

**未改动任何既有公开接口行为**：`DigestionService` / `gate` / `shadow` / `internalize` / `cases` /
`ui_panels` 一个字节未改；未新增事件类型；未新增审计动作；未改面板落位口径。

---

## 四、面板出真数（步骤 4 专项）

### 4.1 演示前真实运行时盘点（"为 0 的原因"定位证据）

对部署运行时**只读**盘点（本任务开工时）：

| 观测点 | 实测 | 结论 |
|---|---|---|
| `agent/data/tool_trace.db::unified_traces` | **225 行全部 `capability_id=''`**（200 success / 25 error） | 真实使用落了**任务级** Trace，能力级 **0 条** |
| `data/events/events.jsonl` 中 `digest.stage` | **0 条**（仅 `events-2026-09-11.jsonl` 有 29 条 S3-01 入轨历史） | 消化链从未在真实运行时目录上跑过 |
| 链式审计中 `digest.*`（subject=`capability:cp.builtin.read_file`） | **0 条** | 内化条件①数据源从未写入 |
| `data/digestion/{cases,shadow,promote_pr}` | **目录不存在** | 链路三个产出目录从未创建 |

**定位结论：属"事件未落"，不是"面板未消费"**。根因：S2-01 的能力级 Trace 透传点只在
`agent/tool_calling.py::ToolCaller._execute_safe` 内生效（要求 `TraceContext.current()` 非空），
本部署的真实工具调用路径未经过它 ⇒ 能力级行缺失 ⇒ `capability.collect_rows()` 样本集恒空 ⇒
`digest.stage` 恒不发射 ⇒ 面板三列恒 0。

**修复与留证**：新增采集侧补位模块 `real_capture.py`（真实调用 + 能力级 Trace 落账），并把真实链路
跑在**真实运行时目录**上；修复后同一读法（面板默认口径）三列非 0（§4.3）。

### 4.2 读法 A：显式目录口径

```
泳道：轨迹采集 30 ｜ 模式挖掘 0 ｜ Skill 生成 0 ｜ 验收 42 ｜ 灰度 42
digest_stage_events=114  applied_migrations=31  shadow_runs=21
internalize_decisions=9  internalize_rate=1.0  capabilities_touched=28
stage_distribution={"shadow": 1, "borrowed": 31}
```

### 4.3 读法 B：面板默认口径 + 部署根代码（= 人工打开面板所见）

```powershell
cd C:\Users\Administrator\agent
python .worktrees\s705\scripts\s705_panel_probe.py --use-env-defaults `
       --code-root C:\Users\Administrator\agent --days 30
```

输出与读法 A **逐字一致**（含每个数字的 `source`/`formula`）。灰度卡片 21 条、内化决策卡片 9 条：
例 `cp.builtin.read_file｜samples=47｜pass_rate=1.0｜judge_kind=deterministic_local(llm_unavailable)｜p99_wall候选=0.121ms`。

> **交叉验证的意义**：读法 A 用 worktree 代码显式传目录，读法 B 用**部署根代码 + 面板自身默认解析**。
> 两者一致 ⇒ 面板在部署根上按默认路径读到的就是真实数字，不存在"脚本自证"。

### 4.4 口径纪律核对

| 纪律 | 实测 |
|---|---|
| 每数字带 `source`+`formula` | ✅ 全部非空（面板 `metric()` 单一出口） |
| 缺数据源记 `None` 不以 0 冒充 | ✅ 演示前 `internalize_rate=None`；空 `stage_distribution={}` |
| 样本 <20 只披露不考核 | ✅ `internalize_decisions=9` / `internalize_rate=1.0` 带 `insufficient_sample=true`、`disclosure_only=true` |
| 不可追溯百分比禁止上屏 | ✅ `traceable=true` 全绿 |
| 无演示填充 | ✅ 未向面板注入任何常量、未做面板特判 |

### 4.5 两列仍为 0 的如实说明（**未修**）

「模式挖掘」「Skill 生成」两列为 0，原因是 `ui_panels/data.py::_lane_of()` 的落位口径：
`skill.generated` 事件载荷不含 `scope`/`to_stage` ⇒ 落位到「轨迹采集」列。本轮**真实产出了**
draft SKILL.md 与 `skill.generated` 事件（`report.events` 非空），故这是**面板落位口径**问题而非"没有产出"。
按任务书步骤 4「定位原因并留证」办理：已定位、已留证，**归属 S6-01 面板口径/S7-06 残留清理**，
本任务不越权改面板口径（改口径会同时影响既有 20/20 验收结论）。

---

## 五、前置配置动作（唯一一处人为改动，须披露）

| 项 | 前 | 后 | 依据 |
|---|---|---|---|
| `cp.builtin.read_file.trust.data_class` | `null` | `internal` | S1-02 trust 回填：只读本地仓库文件、不外发 |

未回填则内化条件⑥ 判 `unknown` ⇒ **一票否决**（引擎从严，是设计行为）。本动作是**分类回填**，
不涉及任何阈值/数值调整，前后值 + actor(`s705_demo`) + 理由入链式审计与证据文件
（`s705_链证据.json::trust_backfill`）。

---

## 六、质量证据（本地门禁）

| 门禁项 | 结果 |
|---|---|
| `pytest`（digestion 全 12 套 + S3-01 交接 + S4-01 promote 链 + S6-01 面板 + S7-05 新增） | **911 passed / 0 failed / 4 skipped**（跳过项为 S3-01 交接用例的"运行时台账不存在（CI 冷启动）"分支，与本任务无关） |
| `pytest tests/unit/test_eval_{anchor,baseline,cases,checkers,datasets,metrics,runner}.py` | **302 passed / 0 failed / 1 skipped**（`--runslow` 门控） |
| 新增单测 | `tests/unit/test_s7_05_real_data.py` **33 passed / 0 failed** |
| 覆盖率（改动模块，branch） | `real_capture.py` **91.80%**｜`switch_snapshot.py` **89.66%**（≥80% ✅） |
| kwarg 扫描 `--path agent` | **HIGH 0** 处（MEDIUM 15 / LOW 73 全为既有） |
| kwarg 扫描 `--path tests` | **HIGH 0** 处（MEDIUM 2 / LOW 29 全为既有） |
| `mypy` 改动模块（2 新增模块 + 2 新增脚本） | **0 error**（`agent/digestion/__init__.py` 与改动面；仓库既有 500 处历史告警不在改动面） |
| `lint-imports`（importlinter） | **2 kept / 0 broken** |
| L0 锚（演示**前**） | `pass 20 / fail 0`，通过率 **1.0000**，clock 口径 `wall_clock(perf_counter)` |
| L0 锚（演示**后**） | `pass 20 / fail 0`，通过率 **1.0000**（证明演示未破坏既有能力） |
| L1 锚（补充） | 全部场景 1.0000 |
| pre-commit 真实提交场景 | 见 §七（未用 `--no-verify`） |
| 产物漂移检查 | 见 §七（`git status` 逐项核对） |

**实现期发现并修复的真实缺陷（2 项）**：

1. **副作用路径口径不一致 ⇒ 回放副作用层误判**：Windows 下 `os.path.join` 与相对段混用产生
   `C:\ws\out/x.md`，而回放侧 `ReplayEnv._norm_path()` 归一为 `C:/ws/out/x.md` ⇒ 27/27 用例
   `side_effects` 层失败（真实墙钟前的"全过"门恒不通过）。修复：采集侧统一正斜杠绝对路径
   （`real_capture._slash()`），并补回归用例 `test_side_effect_paths_use_forward_slashes`。
2. **包属性被同名函数覆盖 ⇒ 导入语义静默改变**：`agent/digestion/__init__.py` 若导出同名函数
   `switch_snapshot`，包属性会从"子模块"变成"函数"，使 `from agent.digestion import switch_snapshot`
   在**与其他套件同会话**时取到函数（单独跑则取到模块）—— 与其它套件同跑时 7 例失败、单独跑全过。
   修复：不在包级导出该同名函数（并在 `__init__.py` 写明命名纪律）。

---

## 七、提交流程与产物漂移

- 提交：worktree `s705` 内 `git add <具体文件>`（逐文件，未用 `git add -A`）→ `commit` → 主工作区
  `git merge s705/main` → 双远端同点推送 → `cleanup s705`；
- pre-commit：真实提交场景下通过（未使用 `--no-verify`）；
- 产物漂移：提交前后 `git status --short` 逐项核对，运行时产物（`data/**`、`agent/data/tool_trace.db`、
  `data/descriptors.json`、`data/audit/**`）均在 `.gitignore` 覆盖内，**不入库**；
  仓库内新增仅 9 个文件（6 代码 + 3 文档）。

---

## 八、结论

- 任务书 §三 交付物 **6/6 齐备**，§四 验收清单 **10/10 通过**（含 4.5 一条"按实记录、归属他任务"的诚实边界）；
- 「真实数据打通」达成：真实数据（315 条真实任务 / 914 条能力级真实 Trace）→ 六阶段链路全跑通 →
  `stage.promote` PR（本地、待人工合入）→ 面板三列**出真数**（与部署根默认口径交叉验证一致）；
- **未声称**：未声称"已实现真实能力内化"、未声称"生产环境已达稳态节奏"、未声称"多能力内化"
  —— 上述边界与**所需条件**逐条写在 [`真实内化演示记录.md`](真实内化演示记录.md) §七。
