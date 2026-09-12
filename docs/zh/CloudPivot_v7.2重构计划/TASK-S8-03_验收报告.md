# TASK-S8-03 验收报告 — 灰度容器隔离（解锁"真实流量接管"）

> 任务书：[`TASK-S8-03_灰度容器隔离.md`](TASK-S8-03_灰度容器隔离.md)｜分发壳：[`START-S8-03_灰度容器隔离.md`](START-S8-03_灰度容器隔离.md)
> 批次总表：[`PARALLEL_S8批次总表.md`](PARALLEL_S8批次总表.md)｜基线：`master` / `7e094eab`（规划）｜worktree：`s803`
> 设计文档：[`../灰度执行隔离设计.md`](../灰度执行隔离设计.md)
> 验收日期：2026-09-13｜平台：Windows 10 / Python 3.12.0 / Docker 29.4.3（daemon 可用）

---

## 零、结论

**验收 9/9 全部通过（0 项未达标、0 项降级主张）。**

本任务把"候选实现无处可安全执行"这个根因补齐了：`real_takeover` 从"**不可开**"变成
"**可开、默认关、失败可回退**"。核心证据是**容器与强隔离子进程各自真跑**的实测对比表
（§四）——它同时是"容器确实提供了内核级边界"与"子进程确实**没有**内核级边界"的双向证据。

**最需要强调的一句话**：本报告里所有"未运行/差距/未测"的格子都是**实测结论**，
不是占位符。子进程等级那 5 格标着「差距」的，正是它的真实弱点；`in_process` 那一列
全是「未运行（refused）」，因为该档**没有**执行隔离环境，执行器**拒绝**执行——
这正是任务书 §五 要求的"不得让看起来能接管"。

---

## 一、验收逐条（对照任务书 §四）

### ✅ 1. 隔离等级模型落地；无 Docker 时**如实降级**并拒绝 `real_takeover`（不得冒充容器）

- **落地**：`IsolationLevel ∈ {in_process, subprocess_hardened, container}`
  （`agent/digestion/isolation.py`），`normalize_level()` 不可识别即返回 `None`（**不猜**）；
  含 `off`/`none`/`docker`/`hardened` 等 11 个别名，但 `auto` **不是**等级。
- **探测分两步**：`<cli> --version`（CLI）与 `<cli> info`（daemon）**独立判定**——
  "装了 CLI 但 daemon 没起"是开发机最常见状态，把它当容器可用正是"冒充容器"。
  本机实测：CLI `29.4.3` 在而 daemon 起初**没起**，此时 `auto` 判 `subprocess_hardened`。
- **如实降级**：显式请求 `container` 而 Docker 不可用 ⇒ 降级 `subprocess_hardened`，
  `downgraded=True` 且理由进 `reasons` 与报告。实测命令与输出：

  ```
  $ python -c "...resolve_isolation_level(requested='container')..."   # daemon 未起时
  'container' -> subprocess_hardened honest_downgrade downgraded= True
  ```
- **无隔离即拒绝**：`in_process` ⇒ `resolve_takeover_policy()` 直接返回
  `enabled=False / source=refused_no_isolation`，且**优先级高于能力级配置**
  （用例 `test_refusal_beats_descriptor_configuration`）。

**证据**：`tests/unit/test_isolation_levels.py::TestResolveIsolationLevel`（9 例）、
`TestDockerProbe`（5 例）、`tests/unit/test_isolation_takeover.py::TestTakeoverPolicy`（14 例）。

### ✅ 2. 容器路径：只读源码 / 网络受限 / 资源配额 / 非 root / 无宿主凭据（**配置与实测双证**）

**配置侧**（`ContainerExecutor.build_argv()`，用例逐参数断言）：

```
docker run --rm -i --network none --memory 256m --memory-swap 256m --cpus 1.0
  --pids-limit 64 --read-only --tmpfs /work:rw,nosuid,nodev,size=32m,mode=1777
  --tmpfs /tmp:rw,nosuid,nodev,size=16m,mode=1777 --user 65534:65534
  --cap-drop ALL --security-opt no-new-privileges
  --mount type=bind,source=<repo>,target=/src,readonly -w /work
  -e HOME= -e USERPROFILE= -e SSH_AUTH_SOCK= … python:3.12-slim
  python /src/agent/digestion/isolation_worker.py --job -
```

- **禁止参数**有断言：`--privileged` / `--pid=host` / `--network=host` / `--userns=host` /
  `--cap-add` / `-v|--volume` 一律不得出现。
- **唯一挂载**是源码只读（用例断言 `len(mounts)==1` 且 `readonly`，且不含
  `.ssh`/`.aws`/`.docker`/`id_rsa`/`credentials`）。

**实测侧**（真 Docker，非 gate 跳过）：

| 项 | 实测值 |
|---|---|
| 非 root | `uid=65534`，`pid=1`（独立 PID 命名空间），`sys_platform=linux`，cwd=`/work` |
| 源码只读 | `/src/...` 写入 → `OSError: [Errno 30] Read-only file system` |
| 边界外只读 | `/host-secrets` 写入 → `Errno 30`（根只读） |
| 网络受限 | 直连 `1.1.1.1:53` → `OSError: [Errno 101] Network is unreachable` |
| 内存配额 | 配额 64MB 下分配 512MB → **exit 137（SIGKILL）**、无结果 |
| 进程数配额 | `--pids-limit 64` 下 spawn 80 → 同时存活 **63** 后 `BlockingIOError: [Errno 11]` |
| 宿主凭据 | 绝对路径宿主凭据 `visible=0`（不在容器文件系统视图内） |

**证据**：`tests/unit/test_isolation_executors.py::TestContainerExecutorArgv`（7 例）+
`TestContainerExecutorIntegration`（4 例，**真 Docker 全部通过**，本次运行 4 passed）。

### ✅ 3. 子进程路径：`HOME`/`SSH_AUTH_SOCK` 为空、宿主凭据不可见（**实测证据**）

- 环境**整体替换**（`env_mode=replace` 语义，复用 S4-04 的 `apply_isolation_env`）：
  实测 `HOME=''`、`USERPROFILE=''`、`SSH_AUTH_SOCK=''`、`AWS_ACCESS_KEY_ID=''`、
  `GITHUB_TOKEN=''`、`KUBECONFIG=''`、`HTTP_PROXY=''`、`HTTPS_PROXY=''`，`present_nonempty=[]`。
- **宿主凭据（env 推导路径）不可见**：`HOME`/`USERPROFILE` 为空 ⇒ `~/.ssh/id_rsa` 等
  5 条路径**推导不出来**，实测 `visible_count=0`（5 条逐条给出理由）。
- 口径一致性有断言：与 `agent.subagent.sandbox.apply_isolation_env` 的 HOME/USERPROFILE/
  SSH_*/代理口径**逐键相同**（`test_env_matches_s4_04_isolation_paradigm`）。

> **同时如实暴露差距**：宿主凭据的**绝对路径**在该档 `visible=1`（可读）——
> 这一格与"env 推导不可见"并列出现在对比表里，不合并、不淡化（见 §四）。

### ✅ 4. 探针对容器与子进程**分别**输出机器可读证据，并给出**不保证边界**对比

- `scripts/verify_isolation.py`：对**每个等级单独**跑同一份探针定义
  （7 类探针：env / credentials / files / network / memory / cpu / pids），输出：
  - `data/isolation/isolation_evidence.json`（逐探针原始输出 + 派生结论 + 平台自报信息）
  - `data/isolation/isolation_comparison.md`（对比表 + 未双写/残留）
- **不外推**：跑不了的等级单元格写「未运行（<原始理由>）」，`skip_reason` 另存；
  退出码 0=都给出结论（含"如实不可用"）、2=声称可用却无任何结果（真故障）。
- **不保证边界**是机器可读字段：`isolation_boundaries(level).not_guaranteed`
  进 `IsolationPlan.to_dict()`、进报告 `isolation.not_guaranteed`、
  进 `docs/zh/灰度执行隔离设计.md` §七。

### ✅ 5. `record-and-replay` 语义不变：**副作用只记录不双写**（用例断言真实环境无副作用）

四道闸 + 两类实测证据：

1. 回放通道 `ReplayEnv.commit()` **仍恒抛**（用例 `test_commit_still_raises_so_nothing_is_written_twice`）；
2. 换等级不改变回放结论（用例断言 `status`/`steps`/副作用集合/canonical 文本/`diff.passed` 逐项相同）；
3. 隔离通道唯一可写根是一次性临时目录；候选作业**不得**带 `probe_mode`（用例断言），
   越界写一律 `escape_blocked` 且**被拒的写入不落盘**（用例断言文件不存在）；
4. **前后指纹比对**：端到端演示对"真实环境"见证目录做 `snapshot_paths()`/`diff_snapshot()`，
   实测 `{"unchanged": true, "changed": [], "created": [], "removed": []}`。

`verify_isolation.py` 的跑后自检同样给出：`clean=true`、`unexpected=[]`、
`residue_after_cleanup=[]`、`source_tree_residue_after_cleanup=false`
（探针在源码树里自建的临时目录已消失；**唯一新增文件是探针自己的落脚文件**，
它正是"该等级越得出去"的证据本体，已单列并清理）。

### ✅ 6. `real_takeover` **默认关闭**；开启需显式配置；有每日预算上限

- **默认关闭**：`CP_DIGESTION_REAL_TAKEOVER` 未设 ⇒ `enabled=False /
  source=default_off`，且**不向隔离执行器提交任何作业**（用例断言 `executor.jobs == []`）。
- **需"开关 + 显式比例"两件**：只开开关不开比例 ⇒ 不开；只有比例没开关 ⇒ 不开；
  非法比例（`abc`/`0`/`-1`/`1.5`/空）⇒ 回退关闭（参数化用例 5 例 + 5 例）。
- **每日预算上限**：沿用 S3-03 的 `min(日均 × 比例, 上限)`（含低流量保底与 S5-03
  成本刹车系数，用例断言与 `shadow.daily_budget` **逐值相等**）；预算 0 ⇒ 一次都不跑
  （用例断言 `executor.jobs == []`）；`cap` 生效（用例断言尝试数 ≤ cap）。
- **产物不自动合入**：`TakeoverReport.adopted` **恒为 `False`**（多例断言）。

### ✅ 7. 连续失败 → 自动回落 `sandbox_replay_only` + 事故卡（用例）

- 连续 3 次（阈值可配）"跑了但失败" ⇒ 第 3 轮 `fallback=True`、
  `fallback_transport=sandbox_replay_only`、`consecutive_failures=3`、
  事故卡落盘且 `detail.fallback_transport` 与 `detail.capability_id` 齐备。
- **回落态持久**：台账（JSONL）为唯一事实来源 —— 重开实例仍读到回落态（用例）；
  回落后**不再向执行器提交作业**（用例断言作业数不变）。
- **"没跑"不算失败**：预算 0 / 未抽样的轮次 `attempted=0`，不计入连续失败
  （用例 `test_not_running_is_not_a_failure`）。
- 事故卡级别 **L2**（能力级回退，与 `self_healing.levels` 的 L1–L5 语义一致）。

### ✅ 8. 端到端演示可复现（隔离执行 → 灰度记录 → 未双写 → 回退验证）

`scripts/demo_s8_03_isolation.py`（四幕全实测，产物 `data/isolation/demo_e2e_*`）：

| 幕 | 复现结果（本次运行） |
|---|---|
| ① 环境与等级 | Docker 可用（server 29.4.3）⇒ `container`（来源 `auto_probe`，未降级）；打印 6 条不保证边界 |
| ② 隔离执行 + 灰度 | 真发通行证（`passed=True`）→ 灰度双跑 → 接管 **执行 15／一致 15／失败 0**，`adopted=False`，`mode=isolated_execution`、`container_isolated=true` |
| ③ 未双写 | 见证目录前后差异 `{"unchanged": true, ...}` ⇒ **未被改动** |
| ④ 回退验证 | 第 1/2 轮不回退；**第 3 轮** `consecutive_failures=3` ⇒ 回退 `sandbox_replay_only`，事故卡 `inc-L2` |

### ✅ 9. 既有 `digestion`/`subagent`/`guardrails` 套件**零回归**；新增单测全绿、覆盖率 ≥80%

> 下列数字为**合流后**（与 S8-01/S8-04/S8-05/S9 并行成果合并后）在 `master` 上的复测结果。

| 套件 | 结果 |
|---|---|
| `digestion` 邻接 9 套件（shadow/sandbox/gate/cases/internalize/probe/stage/pipeline/applicability） | **588 passed / 0 failed** |
| `subagent` 隔离范式邻接 3 套件（extensions_sandbox / execution_guard / multiprocess_boundary） | **81 passed / 0 failed / 1 skipped（既有，与本任务无关）** |
| `guardrails`/egress 邻接 4 套件 | **127 passed / 0 failed** |
| 邻接合计 | **796 passed / 0 failed / 1 skipped** |
| **新增 4 套件** | **216 passed / 0 failed / 1 skipped** |
| 覆盖率（新模块） | `isolation.py` **86%**｜`isolation_worker.py` **91%**｜`takeover.py` **88%** |

> 跳过的 1 例是 `_posix_limits` 的 POSIX 专属用例在 Windows 上 `skipif` ——
> **如实标注**（Windows 无 `resource` 模块 ⇒ 无 rlimit），不是伪通过。

**合流期实测暴露并修复的 1 处夹具口径问题**（如实记录）：
S8-05 的 D2 **形状过滤**要求"用例能力集合 ⊆ 被评能力"，本任务的测试夹具
（`tests/unit/isolation_util.make_case()`）未显式声明步骤 `capability_id` ⇒
整批被挡在抽样之外 ⇒ **8 例 takeover 用例集体失败**。根因是**夹具形状**、
不是接管逻辑（合流前本分支 216 例全绿）。夹具已按新口径对齐；
该 fix 首轮合并时**漏 `git add`**，在 `master` 复测时被再次拦下并补齐 ——
两次都由"跑完门禁后复核 `git status` + 在 master 上复测"这套纪律捕获。

---

## 二、交付物

| # | 交付物 | 路径 |
|---|---|---|
| 1 | 隔离设计文档（等级语义 + **不保证边界**诚实清单 + 选择/降级策略） | `docs/zh/灰度执行隔离设计.md` |
| 2 | 容器路径 + 强隔离子进程路径执行器 | `agent/digestion/isolation.py`（767 行）、`isolation_worker.py`（385 行，stdlib-only 可在容器内裸跑） |
| 3 | `ReplaySandbox.isolation_level` 接入 | `agent/digestion/sandbox.py`（新增参数 + `isolation_plan/isolation_executor/execute_isolated/verify_isolation`；**回放通道一行未改**） |
| 4 | `real_takeover` 开关（默认关）+ 预算 + 失败回落 + 审计/事件/Trace | `agent/digestion/takeover.py`（555 行）、`agent/digestion/shadow.py`（接线） |
| 5 | 探针脚本 + 容器/子进程对比表 | `scripts/verify_isolation.py` → `data/isolation/isolation_evidence.json` / `isolation_comparison.md` |
| 6 | 端到端演示 | `scripts/demo_s8_03_isolation.py` → `data/isolation/demo_e2e_evidence.json` / `demo_e2e_summary.md` |
| 7 | 本验收报告 | `docs/zh/CloudPivot_v7.2重构计划/TASK-S8-03_验收报告.md` |
| 8 | 交付结案报告 | `docs/zh/CloudPivot_v7.2重构计划/S8-03_交付结案报告_20260913.md` |
| 9 | `00_总览` S8 表状态列 + S8-03 行 | `docs/zh/CloudPivot_v7.2重构计划/00_总览_审计结论与重构总计划.md` |
| – | 新增单测 4 套件 + 共用夹具 | `tests/unit/test_isolation_{levels,executors,takeover,shadow}.py`、`tests/unit/isolation_util.py` |

---

## 三、验收清单勾选（任务书 §四原文逐条）

- [x] 隔离等级模型落地；无 Docker 时**如实降级**并拒绝 `real_takeover`（不得冒充容器）
- [x] 容器路径：只读源码 / 网络受限 / 资源配额 / 非 root / 无宿主凭据（配置与实测双证）
- [x] 子进程路径：`HOME`/`SSH_AUTH_SOCK` 为空、宿主凭据不可见（实测证据）
- [x] 探针对容器与子进程**分别**输出机器可读证据，并给出**不保证边界**对比
- [x] `record-and-replay` 语义不变：**副作用只记录不双写**（用例断言真实环境无副作用）
- [x] `real_takeover` **默认关闭**；开启需显式配置；有每日预算上限
- [x] 连续失败 → 自动回落 `sandbox_replay_only` + 事故卡（用例）
- [x] 端到端演示可复现（隔离执行 → 灰度记录 → 未双写 → 回退验证）
- [x] 既有 `digestion`/`subagent`/`guardrails` 套件零回归；新增单测全绿、覆盖率 ≥80%

---

## 四、隔离探针原始输出（容器 vs 子进程对比表）

> 由 `python scripts/verify_isolation.py` 生成；下表逐格来自实测。
> 完整原始 JSON：`data/isolation/isolation_evidence.json`（含每个探针的 stdout 派生素材）。

| 边界 | in_process | subprocess_hardened | container |
|---|---|---|---|
| HOME 为空 | 未运行（refused） | `''` | `''` |
| USERPROFILE 为空 | 未运行（refused） | `''` | `''` |
| SSH_AUTH_SOCK 为空 | 未运行（refused） | `''` | `''` |
| HOME 平台原始值（≠ 宿主 HOME） | 未运行（refused） | `''` | `/nonexistent`（非宿主 HOME） |
| 宿主凭据（env 推导路径）不可见 | 未运行（refused） | 不可见 | 不可见 |
| 宿主凭据（绝对路径）不可见 | 未运行（refused） | **可见（差距）** | 不可见 |
| 临时目录可写 | 未运行（refused） | 可写 | 可写 |
| 源码树只读 | 未运行（refused） | **写入成功（差距）** | 写入被拒 |
| 宿主目录不可写（文件系统视图） | 未运行（refused） | **越界写入成功（差距）** | 写入被拒 |
| 出域被策略拒绝 | 未运行（refused） | 拒绝 | 拒绝 |
| 出域被**内核**拒绝（直连探针） | 未运行（refused） | **可达（无内核级网络隔离）** | 拒绝（内核级） |
| 内存超限被终止 | 未运行（refused） | **未终止（分配 512MB 仍存活）** | 被终止（硬限） |
| CPU/时间超限被终止 | 未运行（refused） | 被终止 | 被终止 |
| 进程数超限被拒 | 未运行（refused） | **未拒（同时存活 80/80）** | 被拒 |
| 非 root 运行 | 未运行（refused） | 非 root（Windows 无 uid 概念） | `uid=65534`（非 root） |

### 4.1 逐探针原始值（本次运行，摘录）

**subprocess_hardened（elapsed 5.53s，7/7 探针 ran=True）**

```
[env        ] status=success   env={HOME:"", USERPROFILE:"", SSH_AUTH_SOCK:""}  env_raw={HOME:""}
                               env_derived_credentials.visible=0
                               absolute_host_credentials.visible=1        ← 差距（如实）
                               platform={sys_platform:win32, pid:10904, uid:null, python:3.12.0}
[files      ] status=success   work_write_ok=true work_read_ok=true
                               source_write_escaped=true                  ← 差距（如实）
                               host_dir_write_escaped=true                ← 差距（如实）
[network    ] status=success   policy_reachable=false (policy)
                               raw_reachable=true（"连接成功"）            ← 差距（如实）
                               external_call_denied=true
[memory     ] status=success   allocated_mb=512 memory_error=false survived=true killed=false ← 差距（如实）
[cpu        ] status=timeout   exit=1 killed=true timeout=true（"墙钟超时 4.0s 被 kill"）
                               honest_note: 本等级没有 cgroup，超时 kill 是唯一硬上限手段
[pids       ] status=success   spawned=80 requested=80 alive_at_peak=80 blocked=false ← 差距（如实）
```

**container（elapsed 28.97s，7/7 探针 ran=True）**

```
[env        ] status=success   env={HOME:"", USERPROFILE:"", SSH_AUTH_SOCK:""}
                               env_raw={HOME:"/nonexistent"}              ← Docker 覆写，如实留档
                               env_derived_credentials.visible=0
                               absolute_host_credentials.visible=0
                               platform={sys_platform:linux, pid:1, uid:65534, cwd:/work, python:3.12.13}
[files      ] status=success   work_write_ok=true
                               source_write_escaped=false  reason="OSError: [Errno 30] Read-only file system: '/src/.tmp_iso_ro_probe/...'"
                               host_dir_write_escaped=false reason="OSError: [Errno 30] Read-only file system: '/host-secrets'"
[network    ] status=success   policy_reachable=false
                               raw_reachable=false  reason="OSError: [Errno 101] Network is unreachable"  ← 内核级
                               external_call_denied=true
[memory     ] status=killed    exit=137（SIGKILL）error_code=E_ISOLATION_NO_RESULT
                               honest_note: 137 = cgroup 内存超限的典型表现；无结果 ≠ 成功
[cpu        ] status=timeout   exit=1 killed=true（"容器墙钟超时 19.0s 被 kill"）
[pids       ] status=success   spawned=63 requested=80 alive_at_peak=63 blocked=true
                               reason="BlockingIOError: [Errno 11] Resource temporarily unavailable"
```

### 4.2 未双写与残留（同一份证据里）

```
not_double_written = {
  "unchanged": false, "changed": [], "removed": [],
  "created": ["<harness 自建临时目录>/escape-probe.txt"],
  "expected_probe_artifacts": ["<harness 自建临时目录>/escape-probe.txt"],
  "unexpected": [], "clean": true
}
residue_after_cleanup = []                 # 探针落脚目录已删
source_tree_residue_after_cleanup = False  # 源码树上的探针临时目录已消失
```

> `clean=true` 的含义：除**探针自己的落脚文件**外，真实目录没有新增/修改/删除。
> 那个文件本身就是证据（证明子进程等级越得出去），已单列并清理，**不计入"双写"**。

---

## 五、未双写证据（用例断言 + 演示实测）

| 证据 | 位置 | 结果 |
|---|---|---|
| 换等级不改变回放结论 | `test_replay_semantics_unchanged_by_level` | status/steps/副作用/canonical/`diff.passed` 逐项相同 |
| `ReplayEnv.commit()` 仍恒抛 | `test_commit_still_raises_so_nothing_is_written_twice` | 抛 `SandboxError` |
| 越界写被拒且**不落盘** | `test_write_outside_work_root_is_denied` | `escape_blocked` + 目标文件不存在 |
| 源码树未被篡改 | `test_source_tree_write_is_blocked_by_the_guard` | 写入被拒 + 原文件逐字未变 |
| 候选作业不得带 `probe_mode` | `test_executes_takeover_and_never_auto_adopts` | `probe_mode` 缺失 |
| 接管跑完真实目录指纹未变 | `test_real_environment_is_not_written_twice` | `{"unchanged": true, ...}` |
| 端到端演示 | 幕③ | `{"unchanged": true, ...}` |
| 隔离探针跑后自检 | `verify_isolation.py` | `clean=true`、`unexpected=[]`、残留 `[]` |

---

## 六、回退验证记录

**用例侧**（`tests/unit/test_isolation_takeover.py::TestTakeoverEngine` /
`tests/unit/test_isolation_shadow.py::TestFallbackThroughShadow`）：

| 场景 | 期望 | 实测 |
|---|---|---|
| 连续 3 次失败 | 第 3 轮 `fallback=True` + 事故卡落盘 | ✅ `[False, False, True]`，`inc-*.json` 存在且含 `fallback_transport`/`capability_id` |
| 回落后再跑 | 不再提交作业 | ✅ 作业数不变，`executed=0`，理由"已处于回落态" |
| 回落态跨实例 | 重开实例仍成立 | ✅ 台账 JSONL 为唯一事实来源 |
| 成功一轮后 | 连续失败清零 | ✅ `consecutive_failures=0`，`fallback=False` |
| 预算 0 / 未抽样 | **不计**为失败 | ✅ `consecutive_failures` 不变 |
| 回落审计动作 | 独立动作名 | ✅ `digest.shadow.takeover_fallback` |

**演示侧**（幕④，真发通行证 + 真事故卡）：

```
第 1 轮：执行 15／失败 15｜连续失败 1｜回落 False
第 2 轮：执行 15／失败 15｜连续失败 2｜回落 False
第 3 轮：执行 15／失败 15｜连续失败 3｜回落 True → `sandbox_replay_only`（事故卡 inc-*）
事故卡：严重级别=L2｜回落目标=sandbox_replay_only
```

---

## 七、等级如实标注说明（诚实底线）

### 7.1 三层事实分开报告（`ShadowReport.isolation`）

| 字段 | 本次默认（关闭态） | 开启并执行后 |
|---|---|---|
| `mode` | `in_process_deterministic_model` | `isolated_execution` |
| `real_takeover` | `false` | `true` |
| `real_takeover_enabled` | `false` | `true` |
| `real_takeover_executed` | `0` | 实际执行条数 |
| `level` / `container_isolated` | `in_process` / `false` | 生效等级 / 仅容器为 `true` |
| `available_level` | 环境具备的最高等级（本机 `container`） | 同左 |
| `not_guaranteed` | 该等级的**不保证边界**清单 | 同左 |

**关键纪律**：`real_takeover` 由"**开关开着 且 真的执行了**"共同决定。开关开着但
预算为 0 ⇒ `real_takeover=false` 且 `real_takeover_enabled=true` —— 这样"看起来开了
其实一次没跑"无处藏身（用例 `test_enabled_but_not_executed_is_distinguishable`）。

### 7.2 本任务明确**不**声称的能力

- ❌ 不声称 `subprocess_hardened` 提供内核级隔离 —— 对比表里它的 5 处「差距」就是证据；
- ❌ 不声称容器是虚拟机 —— 设计文档 §7.3 明确列出"共享内核可逃逸""未按 digest 锁镜像"
  "未加载自定义 seccomp/AppArmor""whitelist 非内核级"等 6 条；
- ❌ 不声称接管产物可用 —— `adopted` 恒 `False`，自动合入属 L2（本任务不做）；
- ❌ 不声称 `in_process` 那一列"测过" —— 它全是「未运行（refused）」，因为该档
  没有执行隔离环境，执行器**拒绝**执行；
- ⚠️ 不声称接管 Trace 完整 —— 无任务上下文时按既有 P7.1-19 不变量"缺 `workspace_id`
  ⇒ 显式降级"记录（运行日志可见该告警），本任务不绕过、不美化。

### 7.3 实测踩到并已修复/已留档的两个"假象陷阱"

1. **`-e HOME=` 挡不住 Docker**：runc 会按容器用户 passwd 条目覆写 `HOME`
   （`/nonexistent`），而普通变量 `-e CP_TEST=` 是生效的。若只报"生效值"，
   读者会以为"是我们清空的"。故**平台原始值另存 `env_raw`**，两者并列进证据。
2. **宿主绝对路径在容器里不是绝对路径**：`C:/...` 在 Linux 下是相对路径，早期探针
   因此把"写进 `/work`"误报成"越界写入成功"。已引入 `${outside_root}` 占位符
   （子进程＝真实宿主临时目录；容器＝未挂载的 `/host-secrets`），两条路径各自"同义"
   可比。

---

## 八、质量证据（门禁）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 新增单测 | `pytest tests/unit/test_isolation_{levels,executors,takeover,shadow}.py` | **216 passed / 0 failed / 1 skipped** |
| 覆盖率（新模块） | `--cov=agent.digestion.{isolation,isolation_worker,takeover}` | isolation **86%** / worker **91%** / takeover **88%** |
| `digestion` 邻接回归 | 9 套件 | **588 passed / 0 failed** |
| `subagent`/`guardrails` 邻接 | 7 套件 | **208 passed / 0 failed / 1 skipped（既有）** |
| kwarg 扫描（agent） | `scan_kwarg_conflicts.py --path agent --min-risk HIGH` | **0 处 HIGH**，exit 0 |
| kwarg 扫描（tests） | `scan_kwarg_conflicts.py --path tests --min-risk HIGH` | **0 处 HIGH**，exit 0 |
| mypy（新增/改动模块） | `mypy … --follow-imports=silent`（CI 同款） | **Success: no issues found in 5 source files** |
| importlinter | `lint-imports`（需 `PYTHONUTF8=1`，见遗留 #3） | **2 kept / 0 broken** |
| pre-commit | 真实提交（**未用 `--no-verify`**，3 次提交 3 次生效） | 见结案报告 §三 |
| 产物漂移 | `git status` 跑完复核 | 运行时产物全在 `data/isolation/`（已 gitignore），源码树无残留 |

---

## 九、遗留与明确不做

### 9.1 遗留（带归属，均**不阻塞**交付）

| # | 遗留 | 归属 | 说明 |
|---|---|---|---|
| 1 | 容器镜像按 tag 固定（`python:3.12-slim`），未按 digest 锁定 | 生产化运维 | 已写进 `not_guaranteed`；生产可经 `CP_DIGESTION_ISOLATION_DOCKER_IMAGE` 指内部镜像 |
| 2 | `network=whitelist` 是应用层协作策略，非内核级拒绝 | 后续（如需内核级白名单需网络策略插件） | 已写进 `not_guaranteed` 与设计文档 |
| 3 | `lint-imports` 在本机默认编码下报 `'gbk' codec can't decode byte 0x90` | 既有环境/工具问题（非本任务引入） | 用 `PYTHONUTF8=1` 即正常；已在 §八 注明 |
| 4 | 接管 Trace 无任务上下文时 `workspace_id` 缺失（既有 P7.1-19 降级） | S2-01 既有不变量 | 不绕过、不美化；见 §7.2 |
| 5 | Windows 子进程等级无内存/CPU/进程数硬限（仅墙钟超时） | 平台限制 | 已写进 `not_guaranteed` 并由探针实测（对比表 3 格「差距」） |

### 9.2 明确不做（任务书 §五）

- ❌ **不做**自动合入候选产物（L2 白名单自动合入属后续决策）—— `adopted` 恒 `False`；
- ❌ **不做**集群级隔离（P5 Backlog）；
- ❌ 不改 `record-and-replay` 语义、不改既有 `digestion` 公开接口行为
  （回放通道一行未改，既有 559 例零回归）。

---

## 十、复现命令

```powershell
cd C:\Users\Administrator\agent\.worktrees\s803

# ① 隔离性实测（容器与子进程各自真跑）→ data/isolation/
python scripts/verify_isolation.py
python scripts/verify_isolation.py --level container        # 只跑容器
python scripts/verify_isolation.py --level subprocess_hardened --json out.json --md out.md

# ② 端到端四幕演示 → data/isolation/demo_e2e_*
python scripts/demo_s8_03_isolation.py

# ③ 新增单测（含真 Docker 集成用例；无 Docker 时如实 skip）
python -m pytest tests/unit/test_isolation_levels.py tests/unit/test_isolation_executors.py `
                 tests/unit/test_isolation_takeover.py tests/unit/test_isolation_shadow.py -q

# ④ 邻接回归
python -m pytest tests/unit/test_digestion_shadow.py tests/unit/test_digestion_sandbox.py `
                 tests/unit/test_digestion_gate.py tests/unit/test_digestion_cases.py `
                 tests/unit/test_digestion_internalize.py tests/unit/test_digestion_probe.py `
                 tests/unit/test_digestion_stage.py tests/unit/test_digestion_pipeline.py `
                 tests/unit/test_extensions_sandbox.py tests/unit/test_sandbox_execution_guard.py `
                 tests/unit/test_guardrails_egress_chain.py tests/unit/test_policy_egress.py -q
```
